const int thumbPin = 4;
const int pointerPin = 5;
const int middlePin = 6;
const int ringPin = 2;
const int pinkiePin = 3;



void setup() 
{
  Serial.begin(115200);
  //pinMode(adcPin, INPUT);
  analogReadResolution(12);       
  analogSetAttenuation(ADC_6db);  
}



void loop() 
{
  float thumb = analogRead(thumbPin);
  float pointer = analogRead(pointerPin);
  float middle = analogRead(middlePin);
  float ring = analogRead(ringPin);
  float pinkie = analogRead(pinkiePin);
  
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
  delay(50);
}