#include <WiFi.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>
//#include <Servo.h>


const char* ssid = "CALVIN-Student-2G"; 
const char* password = "";


// 2. PENGATURAN MQTT BROKER
const char* mqtt_server = "10.252.251.87"; 

// 3. PENGATURAN PIN
const int ledPin = 23;
const int servoPin = 33;
const int ldrPin = 34;

Servo servoMotor;
WiFiClient espClient;
PubSubClient client(espClient);



long lastMsg = 0; // Timer untuk sensor LDR

void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());
}

// FUNGSI INI BERJALAN SAAT ADA PESAN MQTT MASUK (DARI ASISTEN PYTHON)
void callback(char* topic, byte* message, unsigned int length) {
  Serial.print("Pesan masuk di topik: ");
  Serial.print(topic);
  Serial.print(". Pesan: ");
  String pesanTemp;
  
  for (int i = 0; i < length; i++) {
    Serial.print((char)message[i]);
    pesanTemp += (char)message[i];
  }
  Serial.println();

  // Logika Kontrol Lampu
  if (String(topic) == "nara/kamar/lampu") {
    if(pesanTemp == "nyala"){
      digitalWrite(ledPin, HIGH);
      Serial.println("Lampu menyala!");
    }
    else if(pesanTemp == "mati"){
      digitalWrite(ledPin, LOW);
      Serial.println("Lampu mati!");
    }
  }

  // Logika Kontrol Tirai (Servo)
  if (String(topic) == "nara/kamar/tirai") {
    if(pesanTemp == "buka"){
      servoMotor.write(180); // Putar 180 derajat
      Serial.println("Tirai Terbuka");
    }
    else if(pesanTemp == "tutup"){
      servoMotor.write(0); // Kembali ke 0 derajat
      Serial.println("Tirai Tertutup");
    }
  }
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Menghubungkan ke MQTT Broker...");
    if (client.connect("ESP32Client")) {
      Serial.println("Terhubung!");
      // Setelah terhubung, subscribe ke topik perintah
      client.subscribe("nara/kamar/lampu");
      client.subscribe("nara/kamar/tirai");
    } else {
      Serial.print("Gagal, rc=");
      Serial.print(client.state());
      Serial.println(" coba lagi dalam 5 detik");
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(ledPin, OUTPUT);
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  
  // Set frekuensi servo standar ke 50 Hz
  servoMotor.setPeriodHertz(50);
  servoMotor.attach(servoPin);
  servoMotor.write(0); // Posisi awal tirai tertutup
  
  setup_wifi();
  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // Membaca LDR dan mengirim data setiap 5 detik
  long now = millis();
  if (now - lastMsg > 5000) {
    lastMsg = now;
    int nilaiLDR = analogRead(ldrPin);
    
    // Mengubah nilai integer ke string agar bisa dikirim via MQTT
    char ldrString[8];
    dtostrf(nilaiLDR, 1, 2, ldrString);
    
    Serial.print("Nilai Cahaya: ");
    Serial.println(ldrString);
    client.publish("nara/kamar/sensor_cahaya", ldrString);
  }
}