/*
 * 2단계 배선 확인용 펌웨어.
 *
 * - 부팅하면 스피커에서 "삐" 소리 (앰프/스피커 확인)
 * - 시리얼 모니터에 왼쪽(L)/오른쪽(R) 마이크 음량 막대 표시 (마이크 2개 각각 확인)
 * - RECORD_TEST 1 이면 말을 할 때 3초 녹음 후 바로 재생 (마이크 → 스피커 전체 경로 확인)
 *
 * 마이크 소리를 스피커로 실시간으로 내보내지 않는 이유: 둘이 붙어 있으면 하울링이 난다.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// INMP441 x2: 같은 I2S 버스, L/R 핀으로 채널 구분 (GND=L, 3.3V=R)
// GPIO0/GPIO2 는 부팅 모드 핀이고 보드의 버튼·LED 가 붙어 있어 클럭이 깨진다. 쓰지 말 것.
#define MIC_SCK GPIO_NUM_14
#define MIC_WS GPIO_NUM_15
#define MIC_SD GPIO_NUM_32

// MAX98357A
#define AMP_BCLK GPIO_NUM_26
#define AMP_LRC GPIO_NUM_25
#define AMP_DIN GPIO_NUM_22

#define SAMPLE_RATE 16000
#define FRAMES_PER_READ 256
#define RECORD_SECONDS 3
#define VOLUME 0.2f  // 스피커 볼륨 0.0 ~ 1.0
#define MIC_GAIN_SHIFT 14  // INMP441 32비트 샘플 → 16비트. 작을수록 크게 들림
#define USE_CHANNEL 2  // 녹음에 쓸 마이크: 0 = L, 1 = R, 2 = 둘 평균
#define RECORD_TEST 1  // 1 이면 말소리 감지 시 3초 녹음 후 재생, 0 이면 음량 표시만
#define TRIGGER_ABOVE_NOISE_DB 15.0f  // 주변 소음보다 이만큼 크면 녹음 시작. 너무 자주 녹음되면 올린다

static const char *TAG = "pago";
static i2s_chan_handle_t mic_rx;
static i2s_chan_handle_t amp_tx;

static void init_mic(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &mic_rx));

    i2s_std_config_t cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_SCK,
            .ws = MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = MIC_SD,
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(mic_rx, &cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(mic_rx));
}

static void init_amp(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;  // 보낼 데이터가 없으면 무음
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &amp_tx, NULL));

    i2s_std_config_t cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = AMP_BCLK,
            .ws = AMP_LRC,
            .dout = AMP_DIN,
            .din = I2S_GPIO_UNUSED,
        },
    };
    // 모노 데이터를 L/R 양쪽 슬롯에 똑같이 보낸다. MAX98357A 기본 설정은 (L+R)/2 를 출력하므로
    // 한쪽만 보내면 소리가 절반으로 작아진다.
    cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(amp_tx, &cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(amp_tx));
}

static void play(const int16_t *samples, size_t count)
{
    int16_t chunk[256];
    size_t written;
    for (size_t pos = 0; pos < count; pos += 256) {
        size_t n = count - pos < 256 ? count - pos : 256;
        for (size_t i = 0; i < n; i++) chunk[i] = (int16_t)(samples[pos + i] * VOLUME);
        ESP_ERROR_CHECK(i2s_channel_write(amp_tx, chunk, n * sizeof(int16_t), &written, portMAX_DELAY));
    }
}

static void beep(void)
{
    static int16_t tone[SAMPLE_RATE / 4];  // 0.25초
    for (int i = 0; i < SAMPLE_RATE / 4; i++) {
        tone[i] = (int16_t)(6000 * sinf(2 * M_PI * 880 * i / SAMPLE_RATE));
    }
    play(tone, SAMPLE_RATE / 4);
}

static int16_t to_int16(int32_t sample)
{
    int32_t v = sample >> MIC_GAIN_SHIFT;
    if (v > INT16_MAX) return INT16_MAX;
    if (v < INT16_MIN) return INT16_MIN;
    return (int16_t)v;
}

// 스테레오 32비트 프레임을 읽어 L/R 음량(dBFS)을 계산하고, mono 에 16비트 평균을 채운다.
static void read_mic(int32_t *raw, int16_t *mono, float *db_left, float *db_right)
{
    size_t bytes_read;
    ESP_ERROR_CHECK(i2s_channel_read(mic_rx, raw, FRAMES_PER_READ * 2 * sizeof(int32_t), &bytes_read, portMAX_DELAY));

    double sum_l = 0, sum_r = 0;
    for (int i = 0; i < FRAMES_PER_READ; i++) {
        int16_t l = to_int16(raw[2 * i]);
        int16_t r = to_int16(raw[2 * i + 1]);
        sum_l += (double)l * l;
        sum_r += (double)r * r;
        if (mono) mono[i] = USE_CHANNEL == 0 ? l : USE_CHANNEL == 1 ? r : (int16_t)(((int32_t)l + r) / 2);
    }
    *db_left = 10 * log10f(sum_l / FRAMES_PER_READ / (32768.0 * 32768.0) + 1e-10);
    *db_right = 10 * log10f(sum_r / FRAMES_PER_READ / (32768.0 * 32768.0) + 1e-10);
}

static void print_meter(float db_left, float db_right)
{
    // -70dB(무음) ~ 0dB(최대) 를 막대 30칸으로
    char bar_l[31], bar_r[31];
    int n_l = (int)fmaxf(0, fminf(30, (db_left + 70) * 30 / 70));
    int n_r = (int)fmaxf(0, fminf(30, (db_right + 70) * 30 / 70));
    memset(bar_l, '#', n_l), bar_l[n_l] = 0;
    memset(bar_r, '#', n_r), bar_r[n_r] = 0;
    printf("L %6.1fdB %-30s | R %6.1fdB %-30s\n", db_left, bar_l, db_right, bar_r);
}

static void record_and_play(int32_t *raw)
{
    size_t total = SAMPLE_RATE * RECORD_SECONDS;
    int16_t *recording = malloc(total * sizeof(int16_t));
    if (!recording) {
        ESP_LOGE(TAG, "메모리 부족");
        return;
    }

    ESP_LOGI(TAG, "녹음 시작 (%d초) - 말해보세요", RECORD_SECONDS);
    float db_l, db_r;
    for (size_t pos = 0; pos + FRAMES_PER_READ <= total; pos += FRAMES_PER_READ) {
        read_mic(raw, recording + pos, &db_l, &db_r);
    }
    ESP_LOGI(TAG, "재생");
    play(recording, total);
    free(recording);
    ESP_LOGI(TAG, "끝. 다시 말하면 또 녹음");
}


// 진단: 핀이 실제로 깜빡이는지(신호가 나가는지/들어오는지) 직접 읽어서 센다
static void check_pin(gpio_num_t pin, const char *name)
{
    gpio_input_enable(pin);
    int toggles = 0, highs = 0, last = gpio_get_level(pin);
    for (int i = 0; i < 200000; i++) {
        int v = gpio_get_level(pin);
        toggles += v != last;
        highs += v;
        last = v;
    }
    printf("핀 진단 %-4s GPIO%-2d  변화 %6d회  HIGH 비율 %3d%%\n", name, pin, toggles, highs / 2000);
}

void app_main(void)
{
    init_amp();
    init_mic();
    check_pin(MIC_SCK, "SCK");
    check_pin(MIC_WS, "WS");
    check_pin(MIC_SD, "SD");
    ESP_LOGI(TAG, "스피커 테스트: 삐 소리가 나야 함");
    beep();
    ESP_LOGI(TAG, "마이크 음량 표시 중. 마이크를 하나씩 손가락으로 톡톡 쳐보세요. 말을 하면 3초 녹음 후 재생");

    int32_t *raw = malloc(FRAMES_PER_READ * 2 * sizeof(int32_t));
    float db_l, db_r;
    int frames_since_print = 0;

    // INMP441 은 켜진 직후 잠깐 쓰레기 값을 내보내므로 0.5초 버린다
    float noise_db = -40.0f;
    for (int i = 0; i < SAMPLE_RATE / FRAMES_PER_READ / 2; i++) read_mic(raw, NULL, &db_l, &db_r);

    while (true) {
        read_mic(raw, NULL, &db_l, &db_r);

        // 약 0.2초마다 표시
        if (++frames_since_print >= SAMPLE_RATE / FRAMES_PER_READ / 10) {
            print_meter(db_l, db_r);
            frames_since_print = 0;
        }

        float level = USE_CHANNEL == 0 ? db_l : USE_CHANNEL == 1 ? db_r : fmaxf(db_l, db_r);
        if (RECORD_TEST && level > noise_db + TRIGGER_ABOVE_NOISE_DB) {
            printf("소리 감지: L %.1fdB R %.1fdB (주변 소음 %.1fdB)\n", db_l, db_r, noise_db);
            record_and_play(raw);
            // 재생하는 동안 쌓인 마이크 데이터(스피커 소리)를 버린다
            for (int i = 0; i < SAMPLE_RATE / FRAMES_PER_READ / 2; i++) read_mic(raw, NULL, &db_l, &db_r);
        } else {
            // 조용할 때의 음량을 천천히 따라가며 주변 소음 수준으로 삼는다
            noise_db = 0.98f * noise_db + 0.02f * level;
        }
    }
}
