#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#define MAX_NETWORKS 8
#define UDP_PORT 2323
#define WIFI_TIMEOUT 8000
#define ID 0x04 //Device version ID

const uint8_t thumbPin = 4;
const uint8_t pointerPin = 5;
const uint8_t middlePin = 6;
const uint8_t ringPin = 2; 
const uint8_t pinkiePin = 3;

const uint8_t modePin = 23;

WiFiUDP udp;

Preferences mem;

struct Network_cred { String ssid; String pass;};
Network_cred networks[MAX_NETWORKS];

bool streaming_on = false;
bool ser_ready = false;
bool udp_ready = false;
bool udp_hs_sent  = false;

String last_connected_ssid = "";

bool current_mode = false;

enum LedState { LED_OFF, LED_RED, LED_YELLOW, LED_BLUE, LED_GREEN };
volatile LedState led_state = LED_OFF;

volatile bool mode_interrupt_flag = false;



void IRAM_ATTR mode_isr()
{
  mode_interrupt_flag = true;
}



void load_credentials()
{
  mem.begin("wifi", true);
  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    networks[i].ssid = mem.getString(("ssid" + String(i)).c_str(), "");
    networks[i].pass = mem.getString(("pass" + String(i)).c_str(), "");
  }
  mem.end();
}



void read_credentials()
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



void write_credentials(int index, String ssid, String pass)
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



void clear_credentials(int index)
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



bool wifi_connect()
{
  if (WiFi.status() == WL_CONNECTED)
    return true;

  Serial.println("Scanning for WiFi networks...");

  int found = WiFi.scanNetworks();

  if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }

  if (found <= 0)
  {
    Serial.println("No WiFi networks found.");
    return false;
  }

  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }

    if (networks[i].ssid.length() == 0) continue;

    bool visible = false;
    for (int j = 0; j < found; j++)
      if (WiFi.SSID(j) == networks[i].ssid) { visible = true; break; }
    if (!visible) continue;

    Serial.print("Connecting to: " + networks[i].ssid + " ...");
    WiFi.begin(networks[i].ssid.c_str(), networks[i].pass.c_str());

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT)
    {
      if (mode_interrupt_flag) { WiFi.scanDelete(); return false; }
      delay(50);
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED)
    {
      last_connected_ssid = networks[i].ssid;
      WiFi.scanDelete();
      Serial.println("Connected to: " + networks[i].ssid + " !");
      Serial.print("IP: "); Serial.println(WiFi.localIP());
      Serial.println();
      return true;
    }

    Serial.println("Failed, trying next ...");
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
  streaming_on = false;
  udp_ready    = false;
  udp_hs_sent  = false;
  udp.stop();
  WiFi.disconnect(true);
}



void cmd_exec()
{
  if (!Serial.available())
    return;
  
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd == "ser_ok") { ser_ready = true;  return; }
  //if (cmd == "udp_ok") { udp_ready = true;  return; }
    
  int first = cmd.indexOf(' ');
  int second = cmd.indexOf(' ', first+1);
  int third = cmd.indexOf(' ', second+1);

  String command = (first < 0) ? cmd : cmd.substring(0, first);
  String arg1 = (first < 0) ? "" : cmd.substring(first + 1, second < 0 ? cmd.length() : second);
  String arg2 = (second < 0) ? "" : cmd.substring(second + 1, third < 0 ? cmd.length() : third);
  String arg3 = (third < 0) ? "" : cmd.substring(third + 1);
  

  if (command == "h")
  {
    Serial.println("Cyberkinesis_glove v0.4");
    Serial.println("----------------------------------------------------------------------------------");
    Serial.println("w <index> <ssid> <password> - write new network credentials");
    Serial.println("r                           - show all network credentials");
    Serial.println("c <index>                   - clear network credentials");
    Serial.println("wifi_status                 - scan for available WiFi networks and test connection");
    Serial.println("adc_test                    - read 100 ADC datapoints");
    Serial.println("----------------------------------------------------------------------------------");
    Serial.println("Intersec_Cyberware ©2026");
    Serial.println();
  }
  else if (command == "w")
    write_credentials(arg1.toInt(), arg2, arg3);

  else if (command == "r")
    read_credentials();

  else if (command == "c")
    clear_credentials(arg1.toInt());

  else if (command == "adc_test")
  {
    for (int i=0; i<100; i++)
    {
      read_adc();
      delay(50);
    }  
  }
  else if (command == "ser_hs")
  {
    Serial.flush();
    delay(50);
    Serial.write(ID);
    ser_ready = false;
  }
  else if (command == "wifi_status")
  {
    bool ok = wifi_connect();
    if (ok)
    {
      Serial.println("WiFi is reachable.");
      WiFi.disconnect(true);
      delay(200);
    }
  }
  else { Serial.println("Unknown command '" + command + "'. Type 'h' for help."); }
}



void ser_handshake()
{
  if (!Serial.available()) return;

  String msg = Serial.readStringUntil('\n');
  msg.trim();

  if (msg == "ser_hs")
  {
    Serial.flush();
    delay(50);
    Serial.write(ID);
  }
  else if (msg == "ser_ok")
  {
    ser_ready = true;
    //Serial.println("Serial handshake complete.");
    Serial.println();
  }
}


void read_adc()
{
  int thumb = analogRead(thumbPin);
  int pointer = analogRead(pointerPin);
  int middle = analogRead(middlePin);
  int ring = analogRead(ringPin);
  int pinkie = analogRead(pinkiePin);
  
  Serial.print("Thumb:");
  Serial.print(thumb);
  Serial.print(" ");
  Serial.print("Pointer:");
  Serial.print(pointer);
  Serial.print(" ");
  Serial.print("Middle:");
  Serial.print(middle);
  Serial.print(" ");
  Serial.print("Ring:");
  Serial.print(ring);
  Serial.print(" ");
  Serial.print("Pinkie:");
  Serial.println(pinkie);
}



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

    switch (led_state)
    {
      case LED_RED:    ledWrite(led_flag ? 255 : 0, 0, 0);                         break;
      case LED_YELLOW: ledWrite(led_flag ? 255 : 0, led_flag ? 64 : 0, 0);        break;
      case LED_BLUE:   ledWrite(0, 0, 255);                                         break;
      case LED_GREEN:  ledWrite(0, 255, 0);                                         break;
      case LED_OFF:    ledWrite(0, 0, 0);                                           break;
    }

    vTaskDelay(500 / portTICK_PERIOD_MS);
  }
}



void udp_send()
{
  struct __attribute__((packed)) ADCPacket {
    uint16_t thumb, pointer, middle, ring, pinkie;
  } packet;

  packet.thumb = analogRead(thumbPin);
  packet.pointer = analogRead(pointerPin);
  packet.middle = analogRead(middlePin);
  packet.ring = analogRead(ringPin);
  packet.pinkie = analogRead(pinkiePin);

  udp.beginPacket("255.255.255.255", 2323);  // target device
  udp.write((uint8_t*)&packet, sizeof(packet));
  udp.endPacket();
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



void setup() 
{
  Serial.begin(115200);
  delay(1000);

  pinMode(modePin, INPUT);

  analogReadResolution(12);       
  analogSetAttenuation(ADC_11db); //1.1V range for 0db, 2.2V range for 6db

  WiFi.mode(WIFI_STA);

  udp.begin(UDP_PORT);

  current_mode  = digitalRead(modePin);

  xTaskCreatePinnedToCore(led_task, "led_task", 1024, NULL, 1, NULL, 0);

  attachInterrupt(digitalPinToInterrupt(modePin), mode_isr, CHANGE);

  load_credentials();
}



void loop() 
{
  if (mode_changed())
  {
    if (current_mode == false)
    {
      wifi_stop();
      ser_ready = false;
      //Serial.println();
    }
    else
    {
      ser_ready = false;
      while (Serial.available()) Serial.read();
    }
  }

  if (current_mode == false)
  {
    if (!ser_ready) { ser_handshake(); led_state = LED_RED; }
    else { cmd_exec(); led_state = LED_BLUE; }
    return;
  }

  while (Serial.available()) Serial.read();

  if (WiFi.status() != WL_CONNECTED)
  {
    static unsigned long last_attempt = 0;
    udp_ready    = false;
    udp_hs_sent  = false;
    streaming_on = false;
    led_state    = LED_RED;
    if (millis() - last_attempt > 5000)
    {
      last_attempt = millis();
      wifi_connect();
    }
    return;
  }

  if (!udp_hs_sent)
  {
    udp.beginPacket("255.255.255.255", UDP_PORT);
    udp.write(ID);
    udp.endPacket();
    udp_hs_sent = true;
    return;
  }

  if (!udp_ready)
  {
    led_state = LED_YELLOW;

    int pktSize = udp.parsePacket();
    if (pktSize > 0)
    {
      char buf[32];
      int  len = udp.read(buf, sizeof(buf) - 1);
      if (len > 0)
      {
        buf[len] = '\0';
        String msg = String(buf);
        msg.trim();
        if (msg == "udp_ok")
        {
          udp_ready    = true;
          streaming_on = true;
        }
      }
    }
    return;
  }

  led_state = LED_GREEN;
  if (streaming_on)
  {
    static unsigned long last_send = 0;
    if (millis() - last_send >= 50) { last_send = millis(); udp_send(); }
  }
}