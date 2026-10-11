/*
 * 파고쌤 기기 펌웨어: USB 시리얼(CP2102)로 PC 와 오디오를 주고받는다.
 *
 *   마이크 2개 → 16kHz 16비트 모노로 섞어서 → PC
 *   PC → 16kHz 16비트 모노 → 앰프
 *
 * 프레임 형식 (양방향 동일, 리틀 엔디언):
 *   0xA5 0x5A | type(1) | length(2) | payload(length)
 *
 *   기기 → PC  0x01 마이크 오디오 (int16 PCM)
 *              0x02 hello (펌웨어 버전 문자열, 부팅 시 1회)
 *   PC → 기기  0x11 스피커 오디오 (int16 PCM)
 *              0x12 볼륨 (1바이트, 0~100)
 *              0x13 재생 중인 소리 즉시 중단
 *
 * 배선은 firmware/mic_speaker_test 와 동일 (실측 검증된 핀맵).
 */
#include <string.h>

#include "driver/i2s_std.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#define MIC_SCK GPIO_NUM_14
#define MIC_WS GPIO_NUM_15
#define MIC_SD GPIO_NUM_32

#define AMP_BCLK GPIO_NUM_26
#define AMP_LRC GPIO_NUM_25
#define AMP_DIN GPIO_NUM_22

#define SAMPLE_RATE 16000
#define MIC_FRAMES 256          // 16ms 마다 한 번씩 PC 로 보낸다
#define MIC_GAIN_SHIFT 14       // INMP441 32비트 → 16비트 (x4 증폭)

#define UART_PORT UART_NUM_0
#define UART_BAUD 921600        // 실효 약 90KB/s. 오디오는 한 방향에 32KB/s
#define FW_VERSION "pago-device 0.1"

enum {
    FRAME_MIC = 0x01,
    FRAME_HELLO = 0x02,
    FRAME_SPEAKER = 0x11,
    FRAME_VOLUME = 0x12,
    FRAME_STOP = 0x13,
};

static i2s_chan_handle_t mic_rx;
static i2s_chan_handle_t amp_tx;
static StreamBufferHandle_t speaker_stream;
static volatile int volume_percent = 20;

static void init_i2s(void)
{
    i2s_chan_config_t mic_chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&mic_chan, NULL, &mic_rx));
    i2s_std_config_t mic_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = MIC_SCK, .ws = MIC_WS,
                     .dout = I2S_GPIO_UNUSED, .din = MIC_SD},
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(mic_rx, &mic_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(mic_rx));

    i2s_chan_config_t amp_chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    amp_chan.auto_clear = true;  // 보낼 소리가 없으면 무음
    ESP_ERROR_CHECK(i2s_new_channel(&amp_chan, &amp_tx, NULL));
    i2s_std_config_t amp_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = AMP_BCLK, .ws = AMP_LRC,
                     .dout = AMP_DIN, .din = I2S_GPIO_UNUSED},
    };
    // 모노 데이터를 L/R 양쪽에 보낸다. MAX98357A 기본 출력이 (L+R)/2 라서.
    amp_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(amp_tx, &amp_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(amp_tx));
}

static void send_frame(uint8_t type, const void *payload, uint16_t length)
{
    uint8_t header[5] = {0xA5, 0x5A, type, length & 0xFF, length >> 8};
    uart_write_bytes(UART_PORT, header, sizeof(header));
    uart_write_bytes(UART_PORT, payload, length);
}

static int16_t clip16(int32_t v)
{
    return v > INT16_MAX ? INT16_MAX : v < INT16_MIN ? INT16_MIN : (int16_t)v;
}

// 마이크 → PC
static void mic_task(void *arg)
{
    static int32_t raw[MIC_FRAMES * 2];
    static int16_t mono[MIC_FRAMES];
    float dc_in = 0, dc_out = 0;
    size_t bytes_read;

    while (true) {
        i2s_channel_read(mic_rx, raw, sizeof(raw), &bytes_read, portMAX_DELAY);
        int frames = bytes_read / (2 * sizeof(int32_t));
        for (int i = 0; i < frames; i++) {
            float x = ((raw[2 * i] >> MIC_GAIN_SHIFT) + (raw[2 * i + 1] >> MIC_GAIN_SHIFT)) / 2.0f;
            // INMP441 은 켜진 뒤 몇 초간 큰 DC 오프셋이 있어서 DC 성분을 걸러낸다 (약 20Hz 고역 통과)
            dc_out = x - dc_in + 0.992f * dc_out;
            dc_in = x;
            mono[i] = clip16((int32_t)dc_out);
        }
        send_frame(FRAME_MIC, mono, frames * sizeof(int16_t));
    }
}

// 스피커 버퍼 → 앰프
static void speaker_task(void *arg)
{
    static int16_t chunk[256];
    size_t written;

    while (true) {
        size_t got = xStreamBufferReceive(speaker_stream, chunk, sizeof(chunk), pdMS_TO_TICKS(20));
        if (got == 0) continue;  // auto_clear 가 무음을 채운다
        int n = got / sizeof(int16_t);
        int vol = volume_percent;
        for (int i = 0; i < n; i++) chunk[i] = (int16_t)(chunk[i] * vol / 100);
        i2s_channel_write(amp_tx, chunk, n * sizeof(int16_t), &written, portMAX_DELAY);
    }
}

// PC → 기기 프레임 해석
static void uart_rx_task(void *arg)
{
    static uint8_t payload[2048];
    uint8_t byte, header[3];

    while (true) {
        // 동기 바이트 0xA5 0x5A 를 찾는다
        if (uart_read_bytes(UART_PORT, &byte, 1, portMAX_DELAY) != 1 || byte != 0xA5) continue;
        if (uart_read_bytes(UART_PORT, &byte, 1, pdMS_TO_TICKS(50)) != 1 || byte != 0x5A) continue;
        if (uart_read_bytes(UART_PORT, header, 3, pdMS_TO_TICKS(50)) != 3) continue;

        uint8_t type = header[0];
        uint16_t length = header[1] | (header[2] << 8);
        if (length > sizeof(payload)) continue;  // 깨진 프레임
        if (uart_read_bytes(UART_PORT, payload, length, pdMS_TO_TICKS(200)) != length) continue;

        switch (type) {
        case FRAME_SPEAKER:
            // PC 가 재생 속도에 맞춰 보내므로 보통은 바로 들어간다
            xStreamBufferSend(speaker_stream, payload, length, pdMS_TO_TICKS(500));
            break;
        case FRAME_VOLUME:
            if (length >= 1) volume_percent = payload[0] > 100 ? 100 : payload[0];
            break;
        case FRAME_STOP:
            xStreamBufferReset(speaker_stream);
            break;
        }
    }
}

void app_main(void)
{
    uart_config_t uart_cfg = {
        .baud_rate = UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(UART_PORT, 8192, 8192, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_PORT, &uart_cfg));
    // 콘솔을 꺼서 UART0 핀 연결이 보장되지 않으므로 직접 지정 (TX=1, RX=3, CP2102 에 연결된 핀)
    ESP_ERROR_CHECK(uart_set_pin(UART_PORT, GPIO_NUM_1, GPIO_NUM_3, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    speaker_stream = xStreamBufferCreate(SAMPLE_RATE * sizeof(int16_t), sizeof(int16_t));
    init_i2s();

    send_frame(FRAME_HELLO, FW_VERSION, strlen(FW_VERSION));

    xTaskCreatePinnedToCore(mic_task, "mic", 4096, NULL, 10, NULL, 1);
    xTaskCreatePinnedToCore(speaker_task, "speaker", 4096, NULL, 10, NULL, 1);
    xTaskCreatePinnedToCore(uart_rx_task, "uart_rx", 4096, NULL, 9, NULL, 0);
}
