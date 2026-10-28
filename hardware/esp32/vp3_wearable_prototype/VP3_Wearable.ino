/*
 * VP3 Round Wearable Prototype v0.1.0
 * Board: Waveshare ESP32-S3-Touch-LCD-1.46 (412x412 capacitive touch)
 * Host: existing OTRO VP3 HomeServer, USB CDC serial at 115200 baud
 *
 * Uses official Waveshare Arduino-3.1.1 LVGL_Arduino driver files.
 * IMPORTANT: Does not capture microphone audio or claim hardware mic privacy.
 * Does not use legacy node-controller GPIO 4/5/6/7 assignments.
 */
#include <Arduino.h>
#include <ctype.h>
#include "I2C_Driver.h"
#include "TCA9554PWR.h"
#include "Display_SPD2010.h"
#include "LVGL_Driver.h"
#include "PWR_Key.h"

static const char *PROTOCOL = "vp3-hw-v1";
static const char *CONTROLLER = "vp3-wearable-waveshare-146";
static const unsigned long STATE_PERIOD_MS = 1000;
static const unsigned long HOLD_DELAY_MS = 900;
static const size_t INPUT_LIMIT = 512;

static lv_obj_t *orb = nullptr;
static lv_obj_t *mode_label = nullptr;
static lv_obj_t *orb_label = nullptr;
static String mode = "idle";
static String input;
static uint32_t state_seq = 0;
static uint32_t event_seq = 0;
static unsigned long last_state_at = 0;
static unsigned long pressed_at = 0;
static unsigned long last_power_at = 0;
static bool pressed = false;
static bool held = false;
static bool discarding_line = false;

static void sendHello() {
  Serial.println(
    "{\"type\":\"hello\",\"protocol\":\"vp3-hw-v1\","
    "\"controller_id\":\"vp3-wearable-waveshare-146\","
    "\"firmware\":\"0.1.0\","
    "\"hardware_revision\":\"waveshare-esp32-s3-touch-lcd-1.46\","
    "\"components\":[\"agent_button\",\"status_light\",\"display\"],"
    "\"capabilities\":[\"agent_button\",\"status_light\",\"display\"]}"
  );
}

static void sendState() {
  ++state_seq;
  Serial.print("{\"type\":\"state\",\"seq\":");
  Serial.print(state_seq);
  Serial.print(",\"components\":{\"agent_button\":{\"present\":true,\"ready\":true,\"pressed\":");
  Serial.print(pressed ? "true" : "false");
  Serial.println(
    "},\"status_light\":{\"present\":true,\"ready\":true},"
    "\"display\":{\"present\":true,\"ready\":true}}}"
  );
}

static void sendEvent(const char *action) {
  ++event_seq;
  Serial.print("{\"type\":\"event\",\"seq\":");
  Serial.print(event_seq);
  Serial.print(",\"event\":\"agent_button\",\"action\":\"");
  Serial.print(action);
  Serial.println("\"}");
}

static uint32_t colorForMode(const String &value) {
  if (value == "listening") return 0x36DCC0;
  if (value == "thinking") return 0xB18DFF;
  if (value == "speaking") return 0x52B8FF;
  if (value == "privacy") return 0xE8B967;
  if (value == "error") return 0xFF6B7A;
  if (value == "updating") return 0xFFB474;
  if (value == "off") return 0x39445C;
  return 0x4CA7ED;
}

static void updateModeUI() {
  if (!orb || !mode_label) return;
  const uint32_t color = colorForMode(mode);
  lv_obj_set_style_border_color(orb, lv_color_hex(color), 0);
  lv_obj_set_style_shadow_color(orb, lv_color_hex(color), 0);
  lv_obj_set_style_border_width(orb, pressed ? 10 : 6, 0);
  lv_label_set_text(mode_label, mode.c_str());
  lv_obj_set_style_text_color(mode_label, lv_color_hex(color), 0);
}

static void touchStart() {
  if (pressed) return;
  pressed = true;
  held = false;
  pressed_at = millis();
  updateModeUI();
  sendEvent("press");
  sendState();
}

static void touchStop() {
  if (!pressed) return;
  pressed = false;
  held = false;
  updateModeUI();
  sendEvent("release");
  sendState();
}

static void touchCallback(lv_event_t *event) {
  lv_event_code_t code = lv_event_get_code(event);
  if (code == LV_EVENT_PRESSED) touchStart();
  if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) touchStop();
}

static void buildUI() {
  lv_obj_t *screen = lv_scr_act();
  lv_obj_clean(screen);
  lv_obj_set_style_bg_color(screen, lv_color_hex(0x071322), 0);
  lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

  lv_obj_t *title = lv_label_create(screen);
  lv_label_set_text(title, "VP3");
  lv_obj_set_style_text_color(title, lv_color_hex(0xCBEAFF), 0);
  lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 48);

  orb = lv_obj_create(screen);
  lv_obj_set_size(orb, 236, 236);
  lv_obj_align(orb, LV_ALIGN_CENTER, 0, 0);
  lv_obj_set_style_radius(orb, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(orb, lv_color_hex(0x163252), 0);
  lv_obj_set_style_bg_opa(orb, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(orb, 6, 0);
  lv_obj_set_style_shadow_width(orb, 22, 0);
  lv_obj_set_style_shadow_opa(orb, LV_OPA_30, 0);
  lv_obj_set_style_pad_all(orb, 0, 0);
  lv_obj_add_flag(orb, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(orb, touchCallback, LV_EVENT_ALL, nullptr);

  orb_label = lv_label_create(orb);
  lv_label_set_text(orb_label, "TAP\nTO TALK");
  lv_obj_set_style_text_align(orb_label, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_text_color(orb_label, lv_color_hex(0xF4FAFF), 0);
  lv_obj_center(orb_label);

  mode_label = lv_label_create(screen);
  lv_obj_align(mode_label, LV_ALIGN_BOTTOM_MID, 0, -73);

  lv_obj_t *privacy = lv_label_create(screen);
  lv_label_set_text(privacy, "NO AUDIO  |  PROTOTYPE");
  lv_obj_set_style_text_color(privacy, lv_color_hex(0x90A2B9), 0);
  lv_obj_align(privacy, LV_ALIGN_BOTTOM_MID, 0, -38);
  updateModeUI();
}

// OTRO uses compact, newline-delimited JSON, so look for strict quoted keys.
// This parser intentionally supports only hello and status_light.set.
static String stringField(const String &line, const char *key) {
  String marker = String("\"") + key + "\":\"";
  int start = line.indexOf(marker);
  if (start < 0) return String();
  start += marker.length();
  int end = line.indexOf('"', start);
  if (end <= start || end - start > 80) return String();
  String value = line.substring(start, end);
  for (size_t i = 0; i < value.length(); i++) {
    const char c = value[i];
    if (!(isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' || c == '.')) {
      return String();
    }
  }
  return value;
}

static bool allowedMode(const String &value) {
  return value == "off" || value == "idle" ||
         value == "listening" || value == "thinking" ||
         value == "speaking" || value == "privacy" ||
         value == "error" || value == "updating";
}

static void sendAck(const String &id, bool ok, const char *error = nullptr) {
  Serial.print("{\"type\":\"ack\",\"command_id\":\"");
  Serial.print(id);
  Serial.print("\",\"ok\":");
  Serial.print(ok ? "true" : "false");
  if (error) {
    Serial.print(",\"error\":\"");
    Serial.print(error);
    Serial.print('"');
  }
  Serial.println("}");
}

static void handleLine(const String &line) {
  if (stringField(line, "type") == "hello" &&
      stringField(line, "protocol") == PROTOCOL) {
    sendHello();
    sendState();
    return;
  }

  if (stringField(line, "type") != "command") return;
  String commandId = stringField(line, "command_id");
  if (commandId.isEmpty()) return;
  if (stringField(line, "command") != "status_light.set") {
    sendAck(commandId, false, "unsupported_command");
    return;
  }
  String requested = stringField(line, "mode");
  if (!allowedMode(requested)) {
    sendAck(commandId, false, "invalid_mode");
    return;
  }
  mode = requested;
  updateModeUI();
  sendAck(commandId, true);
}

static void serialLoop() {
  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      if (!discarding_line) handleLine(input);
      input = "";
      discarding_line = false;
    } else if (c != '\r' && !discarding_line) {
      if (input.length() >= INPUT_LIMIT) {
        input = "";
        discarding_line = true;
      } else {
        input += c;
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  unsigned long started = millis();
  while (!Serial && millis() - started < 1500) delay(10);

  PWR_Init();
  I2C_Init();
  TCA9554PWR_Init(0x00);
  Backlight_Init();
  Set_Backlight(50);
  LCD_Init();
  Lvgl_Init();
  buildUI();
  sendHello();
  sendState();
}

void loop() {
  Lvgl_Loop();
  serialLoop();
  unsigned long now = millis();
  if (pressed && !held && now - pressed_at >= HOLD_DELAY_MS) {
    held = true;
    sendEvent("hold");
  }
  if (now - last_state_at >= STATE_PERIOD_MS) {
    last_state_at = now;
    sendState();
  }
  if (now - last_power_at >= 100) {
    last_power_at = now;
    PWR_Loop();
  }
  delay(5);
}
