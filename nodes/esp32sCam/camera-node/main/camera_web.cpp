#include "camera_web.h"

#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "img_converters.h"

#include <cstdlib>

namespace {

constexpr char kTag[] = "camera_web";
constexpr char kStreamBoundary[] = "\r\n--bmsframe\r\n";
constexpr char kStreamContentType[] = "multipart/x-mixed-replace;boundary=bmsframe";
constexpr char kFrameHeader[] = "Content-Type: image/jpeg\r\nContent-Length: %zu\r\n\r\n";

httpd_handle_t s_http_server = nullptr;

esp_err_t root_handler(httpd_req_t *request)
{
    static constexpr char page[] =
        "<!doctype html><html><head><meta name=viewport content='width=device-width'>"
        "<title>BMS ESP32-CAM</title><style>body{margin:0;background:#101722;color:#eef6ff;"
        "font-family:system-ui;text-align:center}img{max-width:100%;height:auto}a{color:#5bc0eb}</style>"
        "</head><body><h1>BMS ESP32-CAM</h1><img src='/stream.mjpeg'>"
        "<p><a href='/snapshot.jpg'>Snapshot</a></p></body></html>";
    httpd_resp_set_type(request, "text/html");
    return httpd_resp_send(request, page, HTTPD_RESP_USE_STRLEN);
}

esp_err_t snapshot_handler(httpd_req_t *request)
{
    camera_fb_t *frame = esp_camera_fb_get();
    if (!frame) {
        httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Camera capture failed");
        return ESP_FAIL;
    }

    uint8_t *jpeg = frame->buf;
    size_t jpeg_length = frame->len;
    bool allocated = false;
    if (frame->format != PIXFORMAT_JPEG) {
        allocated = frame2jpg(frame, 80, &jpeg, &jpeg_length);
    }
    esp_err_t result = allocated || frame->format == PIXFORMAT_JPEG
        ? httpd_resp_set_type(request, "image/jpeg")
        : ESP_FAIL;
    if (result == ESP_OK) {
        httpd_resp_set_hdr(request, "Cache-Control", "no-store");
        result = httpd_resp_send(request, reinterpret_cast<const char *>(jpeg), jpeg_length);
    }
    if (allocated) {
        std::free(jpeg);
    }
    esp_camera_fb_return(frame);
    return result;
}

esp_err_t stream_handler(httpd_req_t *request)
{
    esp_err_t result = httpd_resp_set_type(request, kStreamContentType);
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    while (result == ESP_OK) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (!frame) {
            ESP_LOGE(kTag, "Camera capture failed");
            return ESP_FAIL;
        }
        uint8_t *jpeg = frame->buf;
        size_t jpeg_length = frame->len;
        bool allocated = false;
        if (frame->format != PIXFORMAT_JPEG) {
            allocated = frame2jpg(frame, 80, &jpeg, &jpeg_length);
        }
        char header[64] = {};
        const int header_length = snprintf(header, sizeof(header), kFrameHeader, jpeg_length);
        if ((!allocated && frame->format != PIXFORMAT_JPEG) || header_length <= 0) {
            result = ESP_FAIL;
        } else {
            result = httpd_resp_send_chunk(request, kStreamBoundary, sizeof(kStreamBoundary) - 1);
            if (result == ESP_OK) {
                result = httpd_resp_send_chunk(request, header, header_length);
            }
            if (result == ESP_OK) {
                result = httpd_resp_send_chunk(request, reinterpret_cast<const char *>(jpeg), jpeg_length);
            }
        }
        if (allocated) {
            std::free(jpeg);
        }
        esp_camera_fb_return(frame);
    }
    return result;
}

esp_err_t initialize_camera()
{
    camera_config_t config = {};
    config.pin_pwdn = 32;
    config.pin_reset = -1;
    config.pin_xclk = 0;
    config.pin_sccb_sda = 26;
    config.pin_sccb_scl = 27;
    config.pin_d7 = 35;
    config.pin_d6 = 34;
    config.pin_d5 = 39;
    config.pin_d4 = 36;
    config.pin_d3 = 21;
    config.pin_d2 = 19;
    config.pin_d1 = 18;
    config.pin_d0 = 5;
    config.pin_vsync = 25;
    config.pin_href = 23;
    config.pin_pclk = 22;
    config.xclk_freq_hz = 20000000;
    config.ledc_timer = LEDC_TIMER_0;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    return esp_camera_init(&config);
}

} // namespace

esp_err_t camera_web_start()
{
    esp_err_t result = initialize_camera();
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Camera initialization failed: %s", esp_err_to_name(result));
        return result;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.max_uri_handlers = 6;
    config.stack_size = 6144;
    result = httpd_start(&s_http_server, &config);
    if (result != ESP_OK) {
        esp_camera_deinit();
        return result;
    }

    const httpd_uri_t root = {.uri = "/", .method = HTTP_GET, .handler = root_handler, .user_ctx = nullptr};
    const httpd_uri_t snapshot = {.uri = "/snapshot.jpg", .method = HTTP_GET, .handler = snapshot_handler, .user_ctx = nullptr};
    const httpd_uri_t stream = {.uri = "/stream.mjpeg", .method = HTTP_GET, .handler = stream_handler, .user_ctx = nullptr};
    ESP_ERROR_CHECK(httpd_register_uri_handler(s_http_server, &root));
    ESP_ERROR_CHECK(httpd_register_uri_handler(s_http_server, &snapshot));
    ESP_ERROR_CHECK(httpd_register_uri_handler(s_http_server, &stream));
    ESP_LOGI(kTag, "Camera web server ready on port 80: /snapshot.jpg /stream.mjpeg");
    return ESP_OK;
}
