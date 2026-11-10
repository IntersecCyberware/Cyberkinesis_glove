#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>


#define MAX_NETWORKS 8
#define UDP_PORT 2323
#define WIFI_TIMEOUT_MS 10000
#define SER_HS_TIMEOUT_MS 500
#define UDP_HS_TIMEOUT_MS 500
#define WIFI_SCAN_MS_PER_CHAN 120
#define MAX_SCAN_RESULTS 24

#define ID 0x10 //Device version ID
#define SER_MODE 0x35
#define UDP_MODE 0x37

//RGB LED pinout
#define R_PIN 18
#define G_PIN 19
#define B_PIN 20

#define UDP_WATCHDOG_MS 500
#define SER_WATCHDOG_MS 1000

// Finger calibration button
#define CALIBRATION_PIN 10
#define CALIBRATION_DEBOUNCE_MS 40
#define CALIBRATION_SAMPLES 8

#define THUMB_MAX_PERCENT 49.0f


Preferences mem;

//------------Pins-----------------
const uint8_t thumbPin = 4;
const uint8_t pointerPin = 5;
const uint8_t middlePin = 6;
const uint8_t ringPin = 3;
const uint8_t pinkiePin = 2;

const uint8_t modePin = 11;
//----------------------------------


//----------Finger calibration NVM--------------------
bool finger_calibration_valid = false;

uint16_t finger_zero_adc[5] = {0, 0, 0, 0, 0};

const char* finger_zero_keys[5] = {
  "thumb_zero",
  "pointer_zero",
  "middle_zero",
  "ring_zero",
  "pinkie_zero"
};

bool calibration_last_reading = false;
bool calibration_button_state = false;
unsigned long calibration_last_change = 0;
//----------------------------------------------------


//-----------------------------Function declarations--------------------
float hall_to_percent_thumb(float raw);
float hall_to_percent_fingers(float raw);
float apply_zero_calibration(float raw, uint8_t finger_index, bool is_thumb);

void calibration_button_check();
void finger_calibration_load();
void finger_calibration_save();
void finger_calibrate();
//---------------------------------------------------------------------


//-----------------------------Flags-----------------------------------
volatile bool mode_interrupt_flag = false;

bool current_mode = false;
//---------------------------------------------------------------------


//---------------------------LED vars-------------------------------------
enum LedState { LED_OFF, LED_RED, LED_YELLOW, LED_BLUE, LED_SETTING };

volatile LedState led_state = LED_OFF;
uint32_t led_setting = 0x000000;
//------------------------------------------------------------------------


//--------------------WiFi vars--------------------------
WiFiUDP udp;
String last_connected_ssid = "";

struct Network_cred { String ssid; String pass; };
Network_cred networks[MAX_NETWORKS];

enum WifiResult { WIFI_RES_OK, WIFI_RES_NOT_FOUND, WIFI_RES_AUTH, WIFI_RES_TIMEOUT, WIFI_RES_ABORT };

volatile uint16_t wifi_last_reason = 0;
//-------------------------------------------------------


//---------------------WATCHDOGS vars--------------------
unsigned long last_ser_msg = 0;
unsigned long last_udp_msg = 0;
//-------------------------------------------------------


//----------------INTERRUPT FUNCTIONS------------------------

void IRAM_ATTR mode_isr()
{
  mode_interrupt_flag = true;
}


bool mode_changed()
{
  if (!mode_interrupt_flag) return false;
  mode_interrupt_flag = false;

  bool raw = digitalRead(modePin);
  if (raw == current_mode) return false;

  current_mode = raw;
  return true;
}

//----------------------------------------------------------


//---------------------NVM SETTINGS-----------------------------------

void led_setting_load()
{
  mem.begin("led", true);
  led_setting = mem.getUInt("led_color", 0x000000);
  Serial.println(led_setting, HEX);
  mem.end();
}


void led_setting_write(uint32_t led_color)
{
  mem.begin("led", false);
    mem.putUInt("led_color", led_color);
  mem.end();

  led_setting = led_color;

  Serial.println("LED color set: " + String(led_color, HEX));
  Serial.println();
}


String led_setting_hex()
{
  char buf[8];
  snprintf(buf, sizeof(buf), "%06X", (unsigned int)(led_setting & 0xFFFFFF));
  return String(buf);
}


//---------------------FINGER CALIBRATION NVM-------------------------

void finger_calibration_load()
{
  mem.begin("fcal", true);

  finger_calibration_valid = mem.getBool("valid", false);

  for (int i = 0; i < 5; i++)
  {
    finger_zero_adc[i] = mem.getUShort(finger_zero_keys[i], 0);
  }

  mem.end();
}


void finger_calibration_save()
{
  mem.begin("fcal", false);

  for (int i = 0; i < 5; i++)
  {
    mem.putUShort(finger_zero_keys[i], finger_zero_adc[i]);
  }

  mem.putBool("valid", true);

  mem.end();

  finger_calibration_valid = true;
}


// Average readings to avoid saving a noisy single ADC sample.
uint16_t calibration_read_average(uint8_t pin)
{
  uint32_t total = 0;

  for (int i = 0; i < CALIBRATION_SAMPLES; i++)
  {
    total += analogRead(pin);
    delay(2);
  }

  return total / CALIBRATION_SAMPLES;
}


// Capture and save a new zero point for every finger.
void finger_calibrate()
{
  finger_zero_adc[0] = calibration_read_average(thumbPin);
  finger_zero_adc[1] = calibration_read_average(pointerPin);
  finger_zero_adc[2] = calibration_read_average(middlePin);
  finger_zero_adc[3] = calibration_read_average(ringPin);
  finger_zero_adc[4] = calibration_read_average(pinkiePin);

  finger_calibration_save();
}


// Debounced button: pressed = HIGH.
void calibration_button_check()
{
  bool reading = (digitalRead(CALIBRATION_PIN) == LOW);

  if (reading != calibration_last_reading)
  {
    calibration_last_change = millis();
    calibration_last_reading = reading;
  }

  if (millis() - calibration_last_change >= CALIBRATION_DEBOUNCE_MS)
  {
    if (reading != calibration_button_state)
    {
      calibration_button_state = reading;

      // Calibrate once when the button is pressed.
      if (calibration_button_state == HIGH)
      {
        finger_calibrate();
      }
    }
  }
}


//----------------------------------------------------------------------


//---------------------WiFi CREDENTIALS--------------------------------

void wifi_cred_load()
{
  mem.begin("wifi", true);
  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    networks[i].ssid = mem.getString(("ssid" + String(i)).c_str(), "");
    networks[i].pass = mem.getString(("pass" + String(i)).c_str(), "");
  }

  last_connected_ssid = mem.getString("last_ssid", "");
  mem.end();
}


void wifi_cred_read()
{
  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    Serial.print("SSID " + String(i) + ": ");
    Serial.print(networks[i].ssid.length() > 0 ? networks[i].ssid : "None");
    Serial.print("  Password " + String(i) + ": ");
    Serial.println(networks[i].pass.length() > 0 ? networks[i].pass : "None");
  }
  Serial.println();
}


void wifi_cred_write(int index, String ssid, String pass)
{
  if (index < 0 || index >= MAX_NETWORKS)
  {
    Serial.println("Invalid index");
    return;
  }

  mem.begin("wifi", false);
    mem.putString(("ssid" + String(index)).c_str(), ssid);
    mem.putString(("pass" + String(index)).c_str(), pass);
  mem.end();

  networks[index].ssid = ssid;
  networks[index].pass = pass;

  Serial.println("Credentials saved at index " + String(index));
  Serial.println();
}


void wifi_cred_clear(int index)
{
  if (index < 0 || index >= MAX_NETWORKS)
  {
    Serial.println("Invalid index");
    return;
  }

  bool was_last = networks[index].ssid.length() > 0 && networks[index].ssid == last_connected_ssid;

  mem.begin("wifi", false);
    mem.remove(("ssid" + String(index)).c_str());
    mem.remove(("pass" + String(index)).c_str());
    if (was_last) mem.remove("last_ssid");
  mem.end();

  networks[index].ssid = "";
  networks[index].pass = "";
  if (was_last) last_connected_ssid = "";

  Serial.println("Cleared credentials at index " + String(index));
  Serial.println("@C\tOK\t" + String(index));
  Serial.println();
}


void wifi_cred_report()
{
  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    if (networks[i].ssid.length() > 0)
      Serial.println("@WL\t" + String(i) + "\t" + networks[i].ssid);
  }
  Serial.println("@WL\tEND");
}


int wifi_cred_slot(const String& ssid)
{
  for (int i = 0; i < MAX_NETWORKS; i++)
    if (networks[i].ssid == ssid) return i;

  for (int i = 0; i < MAX_NETWORKS; i++)
    if (networks[i].ssid.length() == 0) return i;

  return -1;
}


void wifi_last_ssid_save(const String& ssid)
{
  if (last_connected_ssid == ssid) return;

  last_connected_ssid = ssid;

  mem.begin("wifi", false);
    mem.putString("last_ssid", ssid);
  mem.end();
}

//----------------------------------------------------------------------



//---------------------LED FUNCTIONS------------------------------------

void ledWrite(uint8_t r, uint8_t g, uint8_t b)
{
  analogWrite(R_PIN, 255-r);
  analogWrite(G_PIN, 255-g);
  analogWrite(B_PIN, 255-b);
}


void led_task(void* param)
{
  bool led_flag = false;

  while (true)
  {
    led_flag = !led_flag;

    uint8_t r = (led_setting >> 16) & 0xFF;
    uint8_t g = (led_setting >> 8) & 0xFF;
    uint8_t b =  led_setting & 0xFF;

    switch (led_state)
    {
      case LED_RED: ledWrite(led_flag ? 255 : 0, 0, 0); break;
      case LED_YELLOW: ledWrite(led_flag ? 255 : 0, led_flag ? 64 : 0, 0); break;
      case LED_BLUE: ledWrite(0, 0, 255); break;
      case LED_SETTING: ledWrite(r, g, b); break;
      case LED_OFF: ledWrite(0, 0, 0); break;
    }

    vTaskDelay(500 / portTICK_PERIOD_MS);
  }
}

//----------------------------------------------------------------------



//---------------------WiFi FUNCTIONS-----------------------------------

void wifi_event(WiFiEvent_t event, WiFiEventInfo_t info)
{
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED &&
      info.wifi_sta_disconnected.reason != WIFI_REASON_ASSOC_LEAVE)
  {
    wifi_last_reason = info.wifi_sta_disconnected.reason;
  }
}


WifiResult wifi_try(const String& ssid, const String& pass)
{
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  wifi_last_reason = 0;
  WiFi.begin(ssid.c_str(), pass.c_str());

  WifiResult result = WIFI_RES_TIMEOUT;
  unsigned long start = millis();

  while (millis() - start < WIFI_TIMEOUT_MS)
  {
    if (WiFi.status() == WL_CONNECTED) return WIFI_RES_OK;

    calibration_button_check();

    if (mode_interrupt_flag) { result = WIFI_RES_ABORT; break; }

    uint16_t reason = wifi_last_reason;

    if (reason == WIFI_REASON_NO_AP_FOUND) { result = WIFI_RES_NOT_FOUND; break; }

    if (reason == WIFI_REASON_AUTH_FAIL ||
        reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
        reason == WIFI_REASON_HANDSHAKE_TIMEOUT)
    {
      result = WIFI_RES_AUTH;
      break;
    }

    delay(20);
  }

  WiFi.disconnect(false);
  return result;
}


String wifi_connect(bool commit = true)
{
  calibration_button_check();

  if (WiFi.status() == WL_CONNECTED)
    return WiFi.SSID();

  Serial.println("Scanning for WiFi networks...");
  int found = WiFi.scanNetworks(false, false, false, WIFI_SCAN_MS_PER_CHAN);

  calibration_button_check();

  if (mode_interrupt_flag) { WiFi.scanDelete(); return ""; }
  if (found <= 0) { Serial.println("No WiFi networks found."); return ""; }

  int order[MAX_NETWORKS];
  bool queued[MAX_NETWORKS] = {false};
  int count = 0;

  if (last_connected_ssid.length() > 0)
  {
    for (int i = 0; i < MAX_NETWORKS; i++)
    {
      if (networks[i].ssid == last_connected_ssid)
      {
        order[count++] = i;
        queued[i] = true;
        break;
      }
    }
  }

  for (int i = 0; i < MAX_NETWORKS; i++)
    if (!queued[i] && networks[i].ssid.length() > 0)
      order[count++] = i;

  for (int n = 0; n < count; n++)
  {
    int i = order[n];

    calibration_button_check();

    if (mode_interrupt_flag) { WiFi.scanDelete(); return ""; }

    bool visible = false;
    for (int j = 0; j < found; j++)
      if (WiFi.SSID(j) == networks[i].ssid) { visible = true; break; }
    if (!visible) continue;

    Serial.print("Connecting to: " + networks[i].ssid + " ...");
    WifiResult result = wifi_try(networks[i].ssid, networks[i].pass);
    Serial.println();

    if (result == WIFI_RES_OK)
    {
      String connected_ssid = networks[i].ssid;
      WiFi.scanDelete();
      if (commit) wifi_last_ssid_save(connected_ssid);
      Serial.println("Connected to: " + connected_ssid + " !");
      Serial.print("IP: "); Serial.println(WiFi.localIP());
      Serial.println();
      return connected_ssid;
    }

    if (result == WIFI_RES_ABORT) { WiFi.scanDelete(); return ""; }

    Serial.println("Failed, trying next...");
  }

  WiFi.scanDelete();
  Serial.println("Could not connect to any stored network.");
  Serial.println();
  return "";
}


void wifi_scan_report()
{
  int found = WiFi.scanNetworks(false, false, false, WIFI_SCAN_MS_PER_CHAN);

  calibration_button_check();

  int sent = 0;
  for (int i = 0; i < found && sent < MAX_SCAN_RESULTS; i++)
  {
    String ssid = WiFi.SSID(i);
    if (ssid.length() == 0) continue;

    bool secure = (WiFi.encryptionType(i) != WIFI_AUTH_OPEN);
    Serial.println("@WS\t" + String(WiFi.RSSI(i)) + "\t" + String(secure ? 1 : 0) + "\t" + ssid);
    sent++;
  }

  WiFi.scanDelete();
  Serial.println("@WS\tEND\t" + String(sent));
}


void wifi_add_network(const String& ssid, const String& pass)
{
  if (ssid.length() == 0 || ssid.length() > 32 ||
      pass.length() > 63 || (pass.length() > 0 && pass.length() < 8))
  {
    Serial.println("@WA\tFAIL\tBAD\t" + ssid);
    return;
  }

  int slot = wifi_cred_slot(ssid);
  if (slot < 0)
  {
    Serial.println("@WA\tFAIL\tFULL\t" + ssid);
    return;
  }

  WiFi.disconnect(false);
  WifiResult result = wifi_try(ssid, pass);
  WiFi.disconnect(false);

  if (result == WIFI_RES_OK)
  {
    wifi_cred_write(slot, ssid, pass);
    wifi_last_ssid_save(ssid);
    Serial.println("@WA\tOK\t" + ssid + "\t" + String(slot));
    return;
  }

  const char* reason = "TIMEOUT";
  if (result == WIFI_RES_NOT_FOUND) reason = "NOT_FOUND";
  else if (result == WIFI_RES_AUTH) reason = "AUTH";
  else if (result == WIFI_RES_ABORT) reason = "ABORT";

  Serial.println("@WA\tFAIL\t" + String(reason) + "\t" + ssid);
}


void wifi_stop()
{
  udp.stop();
  udp.begin(UDP_PORT);
}

//----------------------------------------------------------------------



//--------------------HANDSHAKE FUNCTIONS-------------------------------

bool ser_handshake()
{
  unsigned long last_ser_reply = 0;

  while (true)
  {
    calibration_button_check();

    if (millis() - last_ser_reply >= SER_HS_TIMEOUT_MS)
    {
      last_ser_reply = millis();
      Serial.write(ID);
      Serial.write(SER_MODE);
      Serial.flush();
    }

    if (Serial.available())
    {
      String reply_msg = Serial.readStringUntil('\n');
      reply_msg.trim();
      if (reply_msg == "ser_ok") return true;
    }

    if (mode_changed()) return false;
  }
}


bool udp_handshake()
{
  unsigned long last_udp_reply = 0;

  while (true)
  {
    calibration_button_check();

    if (millis() - last_udp_reply >= UDP_HS_TIMEOUT_MS)
    {
      last_udp_reply = millis();
      Serial.println("Sending UDP handshake");
      udp.beginPacket("255.255.255.255", UDP_PORT);
      udp.write(ID);
      udp.write(UDP_MODE);
      udp.endPacket();
    }

    int pktSize = udp.parsePacket();
    if (pktSize > 0)
    {
      char buf[32];
      int len = udp.read(buf, sizeof(buf) - 1);
      if (len > 0)
      {
        buf[len] = '\0';
        String reply_msg = String(buf);
        reply_msg.trim();
        if (reply_msg == "udp_ok") return true;
      }
    }

    if (mode_changed()) return false;
  }
}

//--------------------------------------------------



//----------------WATCHDOGS FUNCTIONS-----------------

bool ser_watchdog()
{
  return Serial && (millis() - last_ser_msg < SER_WATCHDOG_MS);
}


bool udp_watchdog()
{
  calibration_button_check();

  static unsigned long last_ping = 0;

  if (millis() - last_ping >= UDP_WATCHDOG_MS)
  {
    last_ping = millis();
    udp.beginPacket("255.255.255.255", UDP_PORT);
    udp.print("udp_ping");
    udp.endPacket();
  }

  int pktSize = udp.parsePacket();
  if (pktSize > 0)
  {
    char buf[32];
    int len = udp.read(buf, sizeof(buf) - 1);
    if (len > 0)
    {
      buf[len] = '\0';
      String msg = String(buf);
      msg.trim();
      if (msg == "ping_ok") last_udp_msg = millis();
    }
  }

  return (millis() - last_udp_msg < UDP_WATCHDOG_MS * 2);
}

//-------------------------------------------------------



//-------------------------TERMINAL CONSOLE----------------------------------

void cmd_exec()
{
  calibration_button_check();

  if (!Serial.available())
    return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  last_ser_msg = millis();

  if (cmd == "ser_ok") return;

  if (cmd.startsWith("wa\t"))
  {
    int t1 = cmd.indexOf('\t');
    int t2 = cmd.indexOf('\t', t1 + 1);
    String wa_ssid = (t2 < 0) ? cmd.substring(t1 + 1) : cmd.substring(t1 + 1, t2);
    String wa_pass = (t2 < 0) ? "" : cmd.substring(t2 + 1);
    wifi_add_network(wa_ssid, wa_pass);
    last_ser_msg = millis();
    return;
  }

  int first = cmd.indexOf(' ');
  int second = cmd.indexOf(' ', first+1);
  int third = cmd.indexOf(' ', second+1);

  String command = (first < 0) ? cmd : cmd.substring(0, first);
  String arg1 = (first < 0) ? "" : cmd.substring(first + 1, second < 0 ? cmd.length() : second);
  String arg2 = (second < 0) ? "" : cmd.substring(second + 1, third < 0 ? cmd.length() : third);
  String arg3 = (third < 0) ? "" : cmd.substring(third + 1);

  if (command == "h")
  {
    Serial.println("Cyberkinesis_glove v1.0");
    Serial.println("----------------------------------------------------------------------------------");
    Serial.println("w <index> <ssid> <password> - Write new network credentials");
    Serial.println("r                           - Show all saved network credentials");
    Serial.println("c <index>                   - Clear network credentials");
    Serial.println("wc                          - Check which saved WiFi network is reachable");
    Serial.println("ws                          - Scan WiFi networks");
    Serial.println("wl                          - List saved network SSIDs");
    Serial.println("wa<TAB>ssid<TAB>password    - Test and save a new network");
    Serial.println("ls <color in HEX>           - Set LED color in FFFFFF format");
    Serial.println("lc                          - Check LED color in FFFFFF format");
    Serial.println("adc_test                    - Read 100 ADC datapoints");
    Serial.println("----------------------------------------------------------------------------------");
    Serial.println("Intersec_Cyberware © 2026");
    Serial.println();
  }
  else if (command == "w")
    wifi_cred_write(arg1.toInt(), arg2, arg3);

  else if (command == "r")
    wifi_cred_read();

  else if (command == "c")
    wifi_cred_clear(arg1.toInt());

  else if (command == "adc_test")
  {
    for (int i = 0; i < 100; i++)
    {
      calibration_button_check();

      Serial.printf("Thumb: %4d  Pointer: %4d  Middle: %4d  Ring: %4d  Pinkie: %4d\n",
      analogRead(thumbPin),
      analogRead(pointerPin),
      analogRead(middlePin),
      analogRead(ringPin),
      analogRead(pinkiePin));
      delay(50);
    }
  }
  else if (command == "wc")
  {
    String reachable = wifi_connect(false);
    WiFi.disconnect(false);

    if (reachable.length() > 0)
      Serial.println("@WC\tOK\t" + reachable);
    else
      Serial.println("@WC\tNONE");
  }
  else if (command == "ws")
    wifi_scan_report();

  else if (command == "wl")
    wifi_cred_report();

  else if (command == "ls")
  {
    uint32_t led_hex = strtoul(arg1.c_str(), NULL, 16);
    led_setting_write(led_hex);
    Serial.println("@LS\tOK\t" + led_setting_hex());
  }
  else if (command == "lc")
  {
    led_setting_load();
    Serial.println("@LC\t" + led_setting_hex());
  }
  else { Serial.println("Unknown command '" + command + "'. Type 'h' for help."); }

  last_ser_msg = millis();
}

//-----------------------------------------------------------------------------------



//------------------------UDP DATA SEND--------------------------

void udp_send()
{
  calibration_button_check();

  struct __attribute__((packed)) ADCPacket {
    uint16_t thumb, pointer, middle, ring, pinkie;
  } packet;

  packet.thumb = apply_zero_calibration(
    analogRead(thumbPin) / 1650.0,
    0,
    true
  );

  packet.pointer = apply_zero_calibration(
    analogRead(pointerPin) / 1650.0,
    1,
    false
  );

  packet.middle = apply_zero_calibration(
    analogRead(middlePin) / 1650.0,
    2,
    false
  );

  packet.ring = apply_zero_calibration(
    analogRead(ringPin) / 1650.0,
    3,
    false
  );

  packet.pinkie = apply_zero_calibration(
    analogRead(pinkiePin) / 1650.0,
    4,
    false
  );

  udp.beginPacket("255.255.255.255", UDP_PORT);
  udp.write((uint8_t*)&packet, sizeof(packet));
  udp.endPacket();
}

//------------------------------------------------------------------



//-------------------------DATA PROCESSING--------------------------

// Original calibration curve plus individual zero-offset remapping.
float apply_zero_calibration(float raw, uint8_t finger_index, bool is_thumb)
{
  float current_percent = is_thumb
    ? hall_to_percent_thumb(raw)
    : hall_to_percent_fingers(raw);

  // No saved zero preset: preserve the original behavior.
  if (!finger_calibration_valid)
    return current_percent;

  // Find the original curve's percentage at the saved zero ADC reading.
  float zero_raw = finger_zero_adc[finger_index] / 1650.0f;

  float zero_percent = is_thumb
    ? hall_to_percent_thumb(zero_raw)
    : hall_to_percent_fingers(zero_raw);

  // Anything at or below the calibrated zero is treated as 0%.
  if (current_percent <= zero_percent)
    return 0.0f;

  float remaining_range = 100.0f - zero_percent;

  // Handle calibration taken at the curve's maximum endpoint.
  if (remaining_range <= 0.001f)
    return (raw > zero_raw) ? 100.0f : 0.0f;

  // Map the remaining original curve from zero_percent..100 to 0..100.
  float result =
    ((current_percent - zero_percent) / remaining_range) * 100.0f;

  if (result < 0.0f) result = 0.0f;
  if (result > 100.0f) result = 100.0f;

  return result;
}


float hall_to_percent_thumb_curve(float raw)
{
  static const float calib_raw[19] = {
    0.1, 0.45, 0.76, 0.86, 0.9, 0.93, 0.95, 0.96, 0.97,
    0.98, 0.98, 0.98, 0.99, 0.99, 0.99, 1, 1, 1, 1
  };

  static const float calib_percent[19] = {
    0.00,  5.56, 11.11, 16.67, 22.22, 27.78, 33.33, 38.89, 44.44,
    50.00, 55.56, 61.11, 66.67, 72.22, 77.78, 83.33, 88.89, 94.44, 100.00
  };

  const int n = 19;

  if (raw <= calib_raw[0])     return calib_percent[0];
  if (raw >= calib_raw[n - 1]) return calib_percent[n - 1];

  for (int i = 0; i < n - 1; i++)
  {
    if (raw >= calib_raw[i] && raw <= calib_raw[i + 1])
    {
      float span = calib_raw[i + 1] - calib_raw[i];

      if (span == 0.0f) {
        return (calib_percent[i] + calib_percent[i + 1]) / 2.0f;
      }

      float t = (raw - calib_raw[i]) / span;

      return calib_percent[i] +
             t * (calib_percent[i + 1] - calib_percent[i]);
    }
  }

  return calib_percent[n - 1];
}


float hall_to_percent_thumb(float raw)
{
  float scaled = hall_to_percent_thumb_curve(raw) * (100.0f / THUMB_MAX_PERCENT);

  if (scaled > 100.0f) scaled = 100.0f;

  return scaled;
}


float hall_to_percent_fingers(float raw)
{
  static const float calib_raw[19] = {
    0.088, 0.100, 0.335, 0.595, 0.723, 0.800, 0.848, 0.873, 0.893,
    0.908, 0.918, 0.928, 0.928, 0.933, 0.935, 0.940, 0.950, 0.950, 0.950
  };

  static const float calib_percent[19] = {
    0.00,  5.56, 11.11, 16.67, 22.22, 27.78, 33.33, 38.89, 44.44,
    50.00, 55.56, 61.11, 66.67, 72.22, 77.78, 83.33, 88.89, 94.44, 100.00
  };

  const int n = 19;

  if (raw <= calib_raw[0])     return calib_percent[0];
  if (raw >= calib_raw[n - 1]) return calib_percent[n - 1];

  for (int i = 0; i < n - 1; i++)
  {
    if (raw >= calib_raw[i] && raw <= calib_raw[i + 1])
    {
      float span = calib_raw[i + 1] - calib_raw[i];

      if (span == 0.0f) {
        return (calib_percent[i] + calib_percent[i + 1]) / 2.0f;
      }

      float t = (raw - calib_raw[i]) / span;

      return calib_percent[i] +
             t * (calib_percent[i + 1] - calib_percent[i]);
    }
  }

  return calib_percent[n - 1];
}

//----------------------------------------------------------------------------



void setup()
{
  Serial.begin(115200);
  delay(1000);

  pinMode(modePin, INPUT);

  pinMode(thumbPin, INPUT);
  pinMode(pointerPin, INPUT);
  pinMode(middlePin, INPUT);
  pinMode(ringPin, INPUT);
  pinMode(pinkiePin, INPUT);

  // Calibration button is active HIGH, using the internal pulldown.
  pinMode(CALIBRATION_PIN, INPUT);
  calibration_last_reading = (digitalRead(CALIBRATION_PIN) == LOW);
  calibration_button_state = calibration_last_reading;
  calibration_last_change = millis();

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db); //Shows values in mV

  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(false);
  WiFi.setSleep(false);
  WiFi.onEvent(wifi_event);
  udp.begin(UDP_PORT);

  xTaskCreatePinnedToCore(led_task, "led_task", 1024, NULL, 1, NULL, 0);

  current_mode = digitalRead(modePin);
  attachInterrupt(digitalPinToInterrupt(modePin), mode_isr, CHANGE);

  wifi_cred_load();
  led_setting_load();

  // Load saved finger zero points from NVM.
  finger_calibration_load();
}



void loop()
{
  calibration_button_check();

  if (mode_changed())
  {
    if (current_mode == false)
    {
      udp.stop();
      WiFi.disconnect(true);
      udp.begin(UDP_PORT);
    }

    else while (Serial.available()) Serial.read();
  }

  //led_state = LED_RED;

  if (current_mode == false) //Serial mode
  {
    led_state = LED_OFF;

    if (!ser_handshake()) return;

    led_state = LED_BLUE;

    last_ser_msg = millis();

    while (ser_watchdog())
    {
      calibration_button_check();

      if (mode_changed()) return;
      cmd_exec();
    }

    return;
  }

  else if (current_mode == true) //UDP mode
  {
    led_state = LED_RED;

    if (WiFi.status() != WL_CONNECTED)
    {
      static unsigned long last_attempt = 0;
      if (millis() - last_attempt > WIFI_TIMEOUT_MS)
      {
        last_attempt = millis();
        wifi_connect();
      }
      return;
    }

    led_state = LED_YELLOW;
    if (!udp_handshake()) return;

    last_udp_msg = millis();
    led_state = LED_SETTING;

    while (udp_watchdog())
    {
      calibration_button_check();

      if (mode_changed()) { wifi_stop(); return; }

      static unsigned long last_send = 0;
      if (millis() - last_send >= 50)
      {
        last_send = millis();
        udp_send();
      }
    }

    wifi_stop();
  }
}