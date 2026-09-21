/*
 * VP3 Node Development Controller — VP3 OS Hardware Protocol v1
 *
 * Target: ESP32-S3 / compatible Arduino core
 * Transport: USB serial, 115200 baud, newline-delimited JSON
 *
 * IMPORTANT: The privacy switch must physically interrupt microphone power.
 * Firmware does not implement the privacy cut. It only reads:
 *   1) the switch position; and
 *   2) a post-switch microphone power-sense signal.
 *
 * Pin numbers below are prototype defaults. Change them for the actual board.
 */

#include <Arduino.h>

static const char *VP3_PROTOCOL = "vp3-hw-v1";
static const char *CONTROLLER_ID = "vp3-node-devkit";
static const char *FIRMWARE_VERSION = "0.20.0";
static const char *HARDWARE_REVISION = "node-devkit-a";

static const int PIN_AGENT_BUTTON = 4;       // active LOW, INPUT_PULLUP
static const int PIN_PRIVACY_SWITCH = 5;     // active LOW, INPUT_PULLUP
static const int PIN_MIC_POWER_SENSE = 6;    // HIGH only when post-switch mic rail is powered
static const int PIN_STATUS_LED = 7;         // single LED prototype output

static const unsigned long DEBOUNCE_MS = 35;
static const unsigned long STATE_INTERVAL_MS = 1000;
static const unsigned long HOLD_MS = 900;

static uint32_t eventSeq = 0;
static uint32_t stateSeq = 0;
static unsigned long lastStateAt = 0;

static bool buttonStable = false;
static bool buttonRawLast = false;
static unsigned long buttonChangedAt = 0;
static unsigned long buttonPressedAt = 0;
static bool holdSent = false;

static bool privacyStable = false;
static bool privacyRawLast = false;
static unsigned long privacyChangedAt = 0;

static String lightMode = "idle";
static unsigned long lightTick = 0;
static bool lightBlink = false;

static bool buttonPressed() {
  return digitalRead(PIN_AGENT_BUTTON) == LOW;
}

static bool privacyEngaged() {
  return digitalRead(PIN_PRIVACY_SWITCH) == LOW;
}

static bool microphonePowered() {
  return digitalRead(PIN_MIC_POWER_SENSE) == HIGH;
}

static void printBool(bool value) {
  Serial.print(value ? "true" : "false");
}

static void sendHello() {
  Serial.print("{\"type\":\"hello\",\"protocol\":\"");
  Serial.print(VP3_PROTOCOL);
  Serial.print("\",\"controller_id\":\"");
  Serial.print(CONTROLLER_ID);
  Serial.print("\",\"firmware\":\"");
  Serial.print(FIRMWARE_VERSION);
  Serial.print("\",\"hardware_revision\":\"");
  Serial.print(HARDWARE_REVISION);
  Serial.print("\",\"components\":[\"agent_button\",\"status_light\",\"microphone\",\"speaker\",\"privacy_switch\"],");
  Serial.println("\"capabilities\":[\"status_light\",\"agent_button\",\"privacy_switch\",\"mic_power_cut\",\"mic_power_sense\"]}");
}

static void sendState() {
  const bool privacy = privacyEngaged();
  const bool micPower = microphonePowered();
  const bool micReady = (!privacy && micPower);

  stateSeq++;
  Serial.print("{\"type\":\"state\",\"seq\":");
  Serial.print(stateSeq);
  Serial.print(",\"components\":{");

  Serial.print("\"agent_button\":{\"present\":true,\"ready\":true,\"pressed\":");
  printBool(buttonPressed());
  Serial.print("},");

  Serial.print("\"status_light\":{\"present\":true,\"ready\":true},");

  Serial.print("\"microphone\":{\"present\":true,\"ready\":");
  printBool(micReady);
  Serial.print("},");

  Serial.print("\"speaker\":{\"present\":true,\"ready\":true},");

  Serial.print("\"privacy_switch\":{\"present\":true,\"ready\":true,\"engaged\":");
  printBool(privacy);
  Serial.print(",\"physical_disconnect\":true,\"microphone_powered\":");
  printBool(micPower);
  Serial.println("}}}");
}

static void sendEvent(const char *eventName, const char *action) {
  eventSeq++;
  Serial.print("{\"type\":\"event\",\"seq\":");
  Serial.print(eventSeq);
  Serial.print(",\"event\":\"");
  Serial.print(eventName);
  Serial.print("\",\"action\":\"");
  Serial.print(action);
  Serial.println("\"}");
}

static String extractLightMode(const String &line) {
  static const char *modes[] = {
    "off", "idle", "listening", "thinking",
    "speaking", "privacy", "error", "updating"
  };
  for (const char *mode : modes) {
    String needle = String("\"mode\":\"") + mode + "\"";
    if (line.indexOf(needle) >= 0) {
      return String(mode);
    }
  }
  return String();
}

static String extractCommandId(const String &line) {
  String marker = "\"command_id\":\"";
  int start = line.indexOf(marker);
  if (start < 0) return String();
  start += marker.length();
  int end = line.indexOf('"', start);
  if (end < 0) return String();
  return line.substring(start, end);
}

static void sendAck(const String &commandId, bool ok, const char *error = "") {
  Serial.print("{\"type\":\"ack\",\"command_id\":\"");
  Serial.print(commandId);
  Serial.print("\",\"ok\":");
  printBool(ok);
  if (error && error[0] != '\0') {
    Serial.print(",\"error\":\"");
    Serial.print(error);
    Serial.print("\"");
  }
  Serial.println("}");
}

static void handleSerialLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line.indexOf("\"type\":\"hello\"") >= 0 &&
      line.indexOf(String("\"protocol\":\"") + VP3_PROTOCOL + "\"") >= 0) {
    sendHello();
    sendState();
    return;
  }

  if (line.indexOf("\"command\":\"status_light.set\"") >= 0) {
    String commandId = extractCommandId(line);
    String requested = extractLightMode(line);
    if (requested.length() == 0) {
      sendAck(commandId, false, "invalid_mode");
      return;
    }
    lightMode = requested;
    sendAck(commandId, true);
    return;
  }

  String commandId = extractCommandId(line);
  if (commandId.length() > 0) {
    sendAck(commandId, false, "unsupported_command");
  }
}

static void updateButton() {
  bool raw = buttonPressed();
  unsigned long now = millis();

  if (raw != buttonRawLast) {
    buttonRawLast = raw;
    buttonChangedAt = now;
  }

  if ((now - buttonChangedAt) >= DEBOUNCE_MS && raw != buttonStable) {
    buttonStable = raw;
    if (buttonStable) {
      buttonPressedAt = now;
      holdSent = false;
      sendEvent("agent_button", "press");
    } else {
      sendEvent("agent_button", "release");
      holdSent = false;
    }
    sendState();
  }

  if (buttonStable && !holdSent && (now - buttonPressedAt) >= HOLD_MS) {
    holdSent = true;
    sendEvent("agent_button", "hold");
  }
}

static void updatePrivacySwitch() {
  bool raw = privacyEngaged();
  unsigned long now = millis();

  if (raw != privacyRawLast) {
    privacyRawLast = raw;
    privacyChangedAt = now;
  }

  if ((now - privacyChangedAt) >= DEBOUNCE_MS && raw != privacyStable) {
    privacyStable = raw;
    sendEvent("privacy_switch", privacyStable ? "engaged" : "disengaged");
    sendState();
  }
}

static void updateStatusLight() {
  unsigned long now = millis();

  if (lightMode == "off") {
    digitalWrite(PIN_STATUS_LED, LOW);
    return;
  }
  if (lightMode == "idle" || lightMode == "listening" || lightMode == "speaking") {
    digitalWrite(PIN_STATUS_LED, HIGH);
    return;
  }

  unsigned long interval = 500;
  if (lightMode == "thinking") interval = 180;
  else if (lightMode == "privacy") interval = 850;
  else if (lightMode == "error") interval = 120;
  else if (lightMode == "updating") interval = 300;

  if ((now - lightTick) >= interval) {
    lightTick = now;
    lightBlink = !lightBlink;
    digitalWrite(PIN_STATUS_LED, lightBlink ? HIGH : LOW);
  }
}

void setup() {
  pinMode(PIN_AGENT_BUTTON, INPUT_PULLUP);
  pinMode(PIN_PRIVACY_SWITCH, INPUT_PULLUP);
  pinMode(PIN_MIC_POWER_SENSE, INPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);

  digitalWrite(PIN_STATUS_LED, LOW);

  Serial.begin(115200);
  unsigned long started = millis();
  while (!Serial && (millis() - started) < 2500) {
    delay(10);
  }

  buttonRawLast = buttonPressed();
  buttonStable = buttonRawLast;
  privacyRawLast = privacyEngaged();
  privacyStable = privacyRawLast;

  sendHello();
  sendState();
}

void loop() {
  while (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    handleSerialLine(line);
  }

  updateButton();
  updatePrivacySwitch();
  updateStatusLight();

  unsigned long now = millis();
  if ((now - lastStateAt) >= STATE_INTERVAL_MS) {
    lastStateAt = now;
    sendState();
  }

  delay(2);
}
