/*
  ESP32-CAM (AI-Thinker) als USB-Kamera für das Foto-Rätsel.

  Die Kamera hängt per USB-Seriell-Adapter am Mac, WLAN wird nicht benutzt.
  Protokoll (Textzeilen, mit \n abgeschlossen):

    Mac → Kamera   PING          Kamera → Mac   BEREIT
    Mac → Kamera   FOTO          Kamera → Mac   JPEG <bytes> <crc32 hex>\n  danach die JPEG-Bytes
    Mac → Kamera   FOTO B        dasselbe mit Blitz-LED
                                 bei Problemen  FEHLER <text>

  115200 Baud: höhere Raten kamen an diesem USB-Adapter verfälscht an (getestet bis 2 MBaud).
  Ein Foto braucht damit ca. 6–12 s für die Übertragung.

  Hochladen:  esp32cam/  →  pio run -t upload   (oder Arduino-IDE, Board "AI Thinker ESP32-CAM", PSRAM an)
*/
#include "esp_camera.h"

const uint32_t BAUD = 115200;
const int JPEG_QUALITAET = 14;      // 4..63, kleiner = besser, aber größer und langsamer über USB.
                                    // 14 reicht für das grobe Halftone-Raster (40 lpi) locker.

// Pinbelegung AI-Thinker ESP32-CAM
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
#define BLITZ_GPIO_NUM 4

bool kameraOk = false;

uint32_t crc32(const uint8_t* d, size_t n) {  // wie zlib.crc32
  uint32_t c = 0xFFFFFFFF;
  while (n--) {
    c ^= *d++;
    for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320 & -(c & 1));
  }
  return ~c;
}

bool kameraStarten() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = psramFound() ? FRAMESIZE_UXGA : FRAMESIZE_SVGA;  // 1600×1200 braucht PSRAM
  c.jpeg_quality = JPEG_QUALITAET;
  c.fb_count = psramFound() ? 2 : 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // läuft durch → Belichtung ist beim Auslösen schon eingeregelt
  // Das Kameramodul springt nach dem Flashen gelegentlich nicht an → Modul aus/ein schalten, neu versuchen.
  for (int versuch = 0; versuch < 3; versuch++) {
    if (esp_camera_init(&c) == ESP_OK) {
      // Graustufen: das Rätsel ist ohnehin schwarz-weiß, die JPEGs werden kleiner (= schneller
      // über USB) und der typische Grünstich der OV2640 entfällt.
      esp_camera_sensor_get()->set_special_effect(esp_camera_sensor_get(), 2);
      return true;
    }
    esp_camera_deinit();
    pinMode(PWDN_GPIO_NUM, OUTPUT);
    digitalWrite(PWDN_GPIO_NUM, HIGH);
    delay(200);
    digitalWrite(PWDN_GPIO_NUM, LOW);
    delay(300);
  }
  return false;
}

void foto(bool blitz) {
  if (!kameraOk) { Serial.print("FEHLER Kamera nicht gestartet\n"); return; }
  if (blitz) { digitalWrite(BLITZ_GPIO_NUM, HIGH); delay(150); }
  camera_fb_t* fb = esp_camera_fb_get();  // ein Bild verwerfen: kann von vor dem Befehl stammen
  if (fb) esp_camera_fb_return(fb);
  fb = esp_camera_fb_get();
  if (blitz) digitalWrite(BLITZ_GPIO_NUM, LOW);
  if (!fb) { Serial.print("FEHLER kein Bild von der Kamera\n"); return; }
  Serial.printf("JPEG %u %08x\n", fb->len, crc32(fb->buf, fb->len));
  Serial.write(fb->buf, fb->len);
  Serial.flush();
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(BAUD);
  Serial.setTimeout(200);
  pinMode(BLITZ_GPIO_NUM, OUTPUT);
  digitalWrite(BLITZ_GPIO_NUM, LOW);
  kameraOk = kameraStarten();
  Serial.print(kameraOk ? "BEREIT\n" : "FEHLER Kamera nicht gestartet\n");
}

void loop() {
  if (!Serial.available()) { delay(2); return; }
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "PING") {
    if (!kameraOk) kameraOk = kameraStarten();
    Serial.print(kameraOk ? "BEREIT\n" : "FEHLER Kamera nicht gestartet\n");
  }
  else if (cmd == "FOTO") foto(false);
  else if (cmd == "FOTO B") foto(true);
  else if (cmd.length()) Serial.print("FEHLER unbekannter Befehl\n");
}
