#include <WiFi.h>
#include <WiFiUdp.h>
#include "HX711.h"
#include <EEPROM.h>

// ----------- Ağ Ayarları -----------
const char* ssid = "zeki"; 
const char* password = "77777777";
const char* pc_ip = "172.20.10.3"; 
const int udpPort = 12345;

WiFiUDP udp;

#define DOUT1 14
#define CLK1  15
#define DOUT2 12
#define CLK2  13

HX711 scale1;
HX711 scale2;

float calibration_factor_1 = 180.0;
float calibration_factor_2 = 180.0;

float son_g1 = 0.0;
float son_g2 = 0.0;
bool ilk_okuma = true;
float tolerans = 5.0; 

#define EEPROM_SIZE 64
const int EEPROM_MAGIC_ADDR = 0;
const uint32_t MAGIC = 0xA5A5A5A5;
const int EEPROM_OFFSET1_ADDR = 4;
const int EEPROM_OFFSET2_ADDR = 8;

void saveOffset(int addr, long off) {
  EEPROM.put(EEPROM_MAGIC_ADDR, MAGIC);
  EEPROM.put(addr, off);
  EEPROM.commit(); 
}

bool loadOffset(int addr, long &off) {
  uint32_t m;
  EEPROM.get(EEPROM_MAGIC_ADDR, m);
  if (m != MAGIC) return false;
  EEPROM.get(addr, off);
  return true;
}

String readLine() {
  String s = "";
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (s.length() > 0) break;
      else continue;
    }
    s += c;
  }
  s.trim();
  return s;
}

void setup() {
  Serial.begin(115200);
  EEPROM.begin(EEPROM_SIZE);
  delay(1500);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Wi-Fi Baglanıyor");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nBaglandi!");
  udp.begin(udpPort);

  scale1.begin(DOUT1, CLK1);
  scale1.set_scale(calibration_factor_1);
  
  scale2.begin(DOUT2, CLK2);
  scale2.set_scale(calibration_factor_2);

  long off1, off2;
  if (loadOffset(EEPROM_OFFSET1_ADDR, off1)) {
    scale1.set_offset(off1);
    Serial.println("S1_OFFSET_YUKLENDI");
  } else {
    Serial.println("S1_DARA_YOK (Seri Porttan 't1' gonderin)");
  }
  
  if (loadOffset(EEPROM_OFFSET2_ADDR, off2)) {
    scale2.set_offset(off2);
    Serial.println("S2_OFFSET_YUKLENDI");
  } else {
    Serial.println("S2_DARA_YOK (Seri Porttan 't2' gonderin)");
  }
}

void loop() {
  if (Serial.available()) {
    String cmd = readLine();
    if (cmd == "t1" || cmd == "T1") {
      delay(300);
      scale1.tare();
      saveOffset(EEPROM_OFFSET1_ADDR, scale1.get_offset());
      Serial.println("S1_DARA_ALINDI_VE_KAYDEDILDI");
      ilk_okuma = true; // Daradan sonra kilidi sıfırla
    } 
    else if (cmd == "t2" || cmd == "T2") {
      delay(300);
      scale2.tare();
      saveOffset(EEPROM_OFFSET2_ADDR, scale2.get_offset());
      Serial.println("S2_DARA_ALINDI_VE_KAYDEDILDI");
      ilk_okuma = true; // Daradan sonra kilidi sıfırla
    }
  }

  float g1 = scale1.get_units(3);
  float g2 = scale2.get_units(3);

  if (ilk_okuma) {
    son_g1 = g1;
    son_g2 = g2;
    ilk_okuma = false;
  } else {
    if (abs(g1 - son_g1) > tolerans) {
      son_g1 = g1;
    }
    if (abs(g2 - son_g2) > tolerans) {
      son_g2 = g2;
    }
  }

  
  String packet = String(son_g1, 2) + "," + String(son_g2, 2);
  
  udp.beginPacket(pc_ip, udpPort);
  udp.print(packet);
  udp.endPacket();

  Serial.println("Giden: " + packet);

  delay(100); 
}