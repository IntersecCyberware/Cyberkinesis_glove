#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#define MAX_NETWORKS 8

const uint8_t thumbPin = 4;
const uint8_t pointerPin = 5;
const uint8_t middlePin = 6;
const uint8_t ringPin = 2; 
const uint8_t pinkiePin = 3;

WiFiUDP udp;

Preferences preferences;

String command, arg1, arg2, arg3;

bool wifi_connected_flag = false;
bool streaming_on = false;


void read_credentials()
{
  preferences.begin("wifi", true);

  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    String ssidKey = "ssid" + String(i);
    String passKey = "pass" + String(i);

    if (preferences.isKey(ssidKey.c_str()))
      Serial.print("SSID " + String(i) + ": " + preferences.getString(ssidKey.c_str(), "") + " ");
    else
      Serial.print("SSID " + String(i) + ": None" + " ");

    if (preferences.isKey(passKey.c_str()))
      Serial.println("Password " + String(i) + ": " + preferences.getString(passKey.c_str(), ""));
    else
      Serial.println("Password " + String(i) + ": None");
  }
  Serial.println();

  preferences.end();
}



void write_credentials(int index, String ssid, String pass)
{
  if (index < 0 || index >= MAX_NETWORKS) 
  {
    Serial.println("Invalid index");
    return;
  }

  preferences.begin("wifi", false);   // namespace, read/write mode
    preferences.putString(("ssid" + String(index)).c_str(), ssid);
    preferences.putString(("pass" + String(index)).c_str(), pass);
  preferences.end();

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

  String ssidKey = "ssid" + String(index);
  String passKey = "pass" + String(index);

  preferences.begin("wifi", false);
    preferences.remove(ssidKey.c_str());
    preferences.remove(passKey.c_str());
  preferences.end();

  Serial.println("Cleared credentials at index " + String(index));
  Serial.println();
}



void commander()
{
   if (Serial.available()) 
  {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    
    int first = cmd.indexOf(' ');
    int second = cmd.indexOf(' ', first+1);
    int third = cmd.indexOf(' ', second+1);

    command = cmd.substring(0, first);
    arg1 = cmd.substring(first+1, second);
    arg2 = cmd.substring(second+1, third);
    arg3 = cmd.substring(third+1);
  }

  if (command == "h")
  {
    Serial.println("Cyberkinesis_glove v0.3");
    Serial.println("-----------------------------------------------------------");
    Serial.println("w <index> <ssid> <password> - write new network credentials");
    Serial.println("r - show all network credentials");
    Serial.println("c <index> - clear network credentials");
    Serial.println("start - start data streaming");
    Serial.println("stop - stop data streaming");
    Serial.println("-----------------------------------------------------------");
    Serial.println("Intersec_Cyberware ©2026");
    Serial.println();
    command = "";
  }
  else if (command == "w")
  {
    write_credentials(arg1.toInt(), arg2, arg3);
    command = "";
  }
  else if (command == "r")
  {
    read_credentials();
    command = "";
  }
  else if (command == "c")
  {
    clear_credentials(arg1.toInt());
    command = "";
  }
  else if (command == "start")
  {
    streaming_on = true;
    Serial.println("Streaming ON");
    command = "";
  }
  else if (command == "stop")
  {
    streaming_on = false;
    Serial.println("Streaming OFF");
    command = "";
  }
}



bool wifi_connect()
{
  if (WiFi.status() == WL_CONNECTED)
    return true;

  preferences.begin("wifi", true);

  Serial.println("Scanning for WiFi networks...");

  int foundNetworks = WiFi.scanNetworks();
  if (foundNetworks <= 0)
  {
    Serial.println("No WiFi networks found.");
    preferences.end();
    return false;
  }

  for (int i = 0; i < MAX_NETWORKS; i++)
  {
    String ssidKey = "ssid" + String(i);
    String passKey = "pass" + String(i);
    String ssid = preferences.getString(ssidKey.c_str(), "");
    String pass = preferences.getString(passKey.c_str(), "");

    if (ssid.length() == 0)
      continue; 

    bool net_is_visible = false;

    for (int j = 0; j < foundNetworks; j++)
    {
      if (WiFi.SSID(j) == ssid)
      {
        net_is_visible = true;
        break;
      }
    }

    if (!net_is_visible)
      continue;

    Serial.print("Connecting to: " + ssid + " ...");
    WiFi.begin(ssid.c_str(), pass.c_str());

    unsigned long start = millis();

    while (WiFi.status() != WL_CONNECTED && millis() - start < 8000)
    {
      delay(500);
      Serial.print(".");
    }

    Serial.println();

    if (WiFi.status() == WL_CONNECTED)
    {
      Serial.println("Connected to: " + ssid + " !");
      Serial.print("IP: ");
      Serial.println(WiFi.localIP());
      Serial.println();
      preferences.end();
      return true;
    }

    Serial.println("Failed to connect");
    Serial.println();

    WiFi.disconnect(true);
    delay(500);
  }

  preferences.end();

  Serial.println("Could not connect to any of the stored networks");
  Serial.println();
  return false;   
}



void read_adc(int thumbPin, int pointerPin, int middlePin, int ringPin, int pinkiePin)
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



struct ADCPacket
{
  uint16_t thumb;
  uint16_t pointer;
  uint16_t middle;
  uint16_t ring;
  uint16_t pinkie;
};


void udp_send(int thumbPin, int pointerPin, int middlePin, int ringPin, int pinkiePin)
{
  ADCPacket packet;

  packet.thumb = analogRead(thumbPin);
  packet.pointer = analogRead(pointerPin);
  packet.middle = analogRead(middlePin);
  packet.ring = analogRead(ringPin);
  packet.pinkie = analogRead(pinkiePin);

  udp.beginPacket("255.255.255.255", 2323);  // target device
  udp.write((uint8_t*)&packet, sizeof(packet));
  udp.endPacket();
}



void setup() 
{
  Serial.begin(115200);
  delay(1000);

  WiFi.mode(WIFI_STA);
  wifi_connect();

  udp.begin(0);

  analogReadResolution(12);       
  analogSetAttenuation(ADC_11db); //1.1V range for 0db, 2.2V range for 6db, 
}



void loop() 
{
  commander();

  if (WiFi.status() != WL_CONNECTED)
  {
    wifi_connected_flag = wifi_connect();
  }

  if ((WiFi.status() == WL_CONNECTED) && streaming_on)
  {
    read_adc(thumbPin, pointerPin, middlePin, ringPin, pinkiePin);
    //udp_send(thumbPin, pointerPin, middlePin, ringPin, pinkiePin);
    delay(50);
  }
}