#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>


#define MAX_NETWORKS 8
#define UDP_PORT 2323
#define WIFI_TIMEOUT_MS 5000
#define SER_HS_TIMEOUT_MS 500
#define UDP_HS_TIMEOUT_MS 500

#define ID 0x05 //Device version ID
#define SER_MODE 0x35
#define UDP_MODE 0x37

#define UDP_WATCHDOG_MS 500


Preferences mem;

//------------Pins-----------------
const uint8_t thumbPin = 4;
const uint8_t pointerPin = 5;
const uint8_t middlePin = 6;
const uint8_t ringPin = 2; 
const uint8_t pinkiePin = 3;

const uint8_t modePin = 23;
//----------------------------------

//-------------Flags----------------
volatile bool mode_interrupt_flag = false;

bool current_mode = false;
//----------------------------------

//---------------------------LED vars-------------------------------------
enum LedState { LED_OFF, LED_RED, LED_YELLOW, LED_BLUE, LED_SETTING };

volatile LedState led_state = LED_OFF;
uint32_t led_setting = 0x000000;
//------------------------------------------------------------------------

//--------------------WiFi vars--------------------------
WiFiUDP udp;
String last_connected_ssid = "";

struct Network_cred { String ssid; String pass;};
Network_cred networks[MAX_NETWORKS];
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

  mem.begin("wifi", false);
    mem.remove(("ssid" + String(index)).c_str());
    mem.remove(("pass" + String(index)).c_str());
  mem.end();

  networks[index].ssid = "";
  networks[index].pass = "";

  Serial.println("Cleared credentials at index " + String(index));
  Serial.println();
}

//----------------------------------------------------------------------



//---------------------LED FUNCTIONS------------------------------------

void ledWrite(uint8_t r, uint8_t g, uint8_t b)
{
  rgbLedWrite(RGB_BUILTIN, g, r, b);   // GRB correction
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

bool wifi_connect()
{
  if (WiFi.status() == WL_CONNECTED)
    return true;

  Serial.println("Scanning for WiFi networks...");
  int found = WiFi.scanNetworks();

  if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }
  if (found <= 0) { Serial.println("No WiFi networks found."); return false; }

  if (last_connected_ssid.length() > 0)
  {
    for (int i = 0; i < MAX_NETWORKS; i++)
    {
      if (networks[i].ssid != last_connected_ssid) continue;

      for (int j = 0; j < found; j++)
      {
        if (WiFi.SSID(j) != last_connected_ssid) continue;

        Serial.println("Trying last network: " + last_connected_ssid);
        WiFi.begin(networks[i].ssid.c_str(), networks[i].pass.c_str());

        unsigned long start = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS)
        {
          if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }
          delay(50);
        }
        Serial.println();

        if (WiFi.status() == WL_CONNECTED)
        {
          WiFi.scanDelete();
          Serial.println("Connected to: " + last_connected_ssid + " !");
          Serial.print("IP: "); Serial.println(WiFi.localIP());
          Serial.println();
          return true;
        }

        Serial.println("Last network failed, scanning all...");
        WiFi.disconnect(true);
        delay(200);
        break;
      }
      break;
    }
  }

  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }
    if (networks[i].ssid.length() == 0) continue;
    if (networks[i].ssid == last_connected_ssid) continue; 

    bool visible = false;
    for (int j = 0; j < found; j++)
      if (WiFi.SSID(j) == networks[i].ssid) { visible = true; break; }
    if (!visible) continue;

    Serial.print("Connecting to: " + networks[i].ssid + " ...");
    WiFi.begin(networks[i].ssid.c_str(), networks[i].pass.c_str());

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS)
    {
      if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }
      delay(50);
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED)
    {
      last_connected_ssid = networks[i].ssid;
      WiFi.scanDelete();
      mem.begin("wifi", false);
        mem.putString("last_ssid", last_connected_ssid);
      mem.end();
      Serial.println("Connected to: " + networks[i].ssid + " !");
      Serial.print("IP: "); Serial.println(WiFi.localIP());
      Serial.println();
      return true;
    }

    Serial.println("Failed, trying next...");
    WiFi.disconnect(true);
    delay(200);
  }

  WiFi.scanDelete();
  Serial.println("Could not connect to any stored network.");
  Serial.println();
  return false;
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
  return Serial;
}



bool udp_watchdog()
{
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
  if (!Serial.available())
    return;
  
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  last_ser_msg = millis();

  if (cmd == "ser_ok") return;
    
  int first = cmd.indexOf(' ');
  int second = cmd.indexOf(' ', first+1);
  int third = cmd.indexOf(' ', second+1);

  String command = (first < 0) ? cmd : cmd.substring(0, first);
  String arg1 = (first < 0) ? "" : cmd.substring(first + 1, second < 0 ? cmd.length() : second);
  String arg2 = (second < 0) ? "" : cmd.substring(second + 1, third < 0 ? cmd.length() : third);
  String arg3 = (third < 0) ? "" : cmd.substring(third + 1);
  

  if (command == "h")
  {
    Serial.println("Cyberkinesis_glove v0.5");
    Serial.println("----------------------------------------------------------------------------------");
    Serial.println("w <index> <ssid> <password> - write new network credentials");
    Serial.println("r                           - show all saved network credentials");
    Serial.println("c <index>                   - clear network credentials");
    Serial.println("wifi_status                 - scan for available WiFi networks and test connection");
    Serial.println("led_set <color in HEX>      - set LED color in FFFFFF format");
    Serial.println("adc_test                    - read 100 ADC datapoints");
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
    for (int i=0; i<100; i++)
    {
      Serial.printf("Thumb: %4d  Pointer: %4d  Middle: %4d  Ring: %4d  Pinkie: %4d\n",
      analogRead(thumbPin),
      analogRead(pointerPin),
      analogRead(middlePin),
      analogRead(ringPin),
      analogRead(pinkiePin));
      delay(50);
    }  
  }
  else if (command == "wifi_status")
  {
    udp.stop();
    WiFi.disconnect(true);
  udp.begin(UDP_PORT);
    bool wifi_reachable = wifi_connect();
    if (wifi_reachable)
    {
      Serial.println("WiFi is reachable");
      WiFi.disconnect(true);
      delay(200);
    }
  }
  else if (command == "led_set")
  {
    uint32_t led_hex = strtoul(arg1.c_str(), NULL, 16);
    led_setting_write(led_hex);
  }
  else { Serial.println("Unknown command '" + command + "'. Type 'h' for help."); }
}

//-----------------------------------------------------------------------------------



//------------------------UDP DATA SEND--------------------------

void udp_send()
{
  struct __attribute__((packed)) ADCPacket {
    uint16_t thumb, pointer, middle, ring, pinkie;
  } packet;

  packet.thumb = hall_to_percent_thumb(analogRead(thumbPin) / 1650.0);

  packet.pointer = hall_to_percent_fingers(analogRead(pointerPin) / 1650.0);

  packet.middle = hall_to_percent_fingers(analogRead(middlePin) / 1650.0);

  packet.ring = hall_to_percent_fingers(analogRead(ringPin) / 1650.0);

  packet.pinkie = hall_to_percent_fingers(analogRead(pinkiePin) / 1650.0);

  udp.beginPacket("255.255.255.255", UDP_PORT);
  udp.write((uint8_t*)&packet, sizeof(packet));
  udp.endPacket();
}

//------------------------------------------------------------------



//-------------------------DATA PROCESSING--------------------------

float hall_to_percent_thumb(float raw)
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
      return calib_percent[i] + t * (calib_percent[i + 1] - calib_percent[i]);
    }
  }

  return calib_percent[n - 1];
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
      return calib_percent[i] + t * (calib_percent[i + 1] - calib_percent[i]);
    }
  }

  return calib_percent[n - 1];
}

//----------------------------------------------------------------------------



void setup() 
{
  Serial.begin(115200);
  delay(1000);

  pinMode(modePin, INPUT_PULLDOWN);

  pinMode(thumbPin, INPUT);
  pinMode(pointerPin, INPUT);
  pinMode(middlePin, INPUT);
  pinMode(ringPin, INPUT);
  pinMode(pinkiePin, INPUT);

  analogReadResolution(12);       
  analogSetAttenuation(ADC_11db); //Shows values in mV

  WiFi.mode(WIFI_STA);
  udp.begin(UDP_PORT);

  xTaskCreatePinnedToCore(led_task, "led_task", 1024, NULL, 1, NULL, 0);

  current_mode  = digitalRead(modePin);
  attachInterrupt(digitalPinToInterrupt(modePin), mode_isr, CHANGE);

  wifi_cred_load();
  led_setting_load();
}



void loop() 
{

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
      if (mode_changed()) { wifi_stop(); return; }
      static unsigned long last_send = 0;
      if (millis() - last_send >= 50) { last_send = millis(); udp_send(); }
    }

    wifi_stop();
  }
}