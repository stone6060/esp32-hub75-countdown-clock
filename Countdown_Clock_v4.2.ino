#include <WiFi.h>
#include <ArduinoJson.h>
#include <SPIFFS.h>
#include <vector>
#include <time.h> 
#include <driver/dac.h> // 用于修复 GPIO 25

// 引入 DMA 驱动库和字模
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include "FontData.h"

// ================= 1. 硬件配置 =================
#define PANEL_RES_X 64      
#define PANEL_RES_Y 32      
#define PANEL_CHAIN 1       

// 引脚定义 (使用您确认正常的引脚)
#define R1_PIN 25
#define G1_PIN 26
#define B1_PIN 27
#define R2_PIN 14
#define G2_PIN 17
#define B2_PIN 13
#define A_PIN 32
#define B_PIN 33
#define C_PIN 21
#define D_PIN 15
#define E_PIN -1 
#define LAT_PIN 4
#define OE_PIN 22
#define CLK_PIN 16 

MatrixPanel_I2S_DMA *dma_display = nullptr;
uint16_t TEXT_COLOR = 0xFFFF; // 默认白色

// ================= 2. 全局变量 =================
const char* wifiConfigPath = "/wifi_config.json";
const char* csvPath = "/countdown_data.csv";
const char* ntpServer = "ntp1.aliyun.com";
const long  gmtOffset_sec = 8 * 3600; // 东八区 UTC+8
const int   daylightOffset_sec = 0;   

String wifiSSID = "";
String wifiPassword = "";
bool wifiConnected = false;

struct Countdown {
  int id;
  time_t startTime;
  long durationSeconds;
  bool isActive;
  bool isCompleted;
  time_t endTime;
  String debugTime; // 用于串口调试打印
};

std::vector<Countdown> countdowns;
enum Mode { CLOCK_MODE, COUNTDOWN_MODE };
Mode currentMode = CLOCK_MODE;

// ================= 3. 绘图函数 =================

void drawDigit(int x_offset, int num) {
  int shift_offset = 2; 
  for (int y = 0; y < 15; y++) {
    uint16_t rowData = get_bitmap_row_data(num, y + shift_offset); 
    for (int x = 0; x < 15; x++) {
      if ((rowData >> (14 - x)) & 0x01) dma_display->drawPixel(x_offset + x, 1 + y, TEXT_COLOR); 
    }
  }
  for (int y = 0; y < 15; y++) {
    uint16_t rowData = get_bitmap_row_data(num, 15 + y + shift_offset); 
    for (int x = 0; x < 15; x++) {
      if ((rowData >> (14 - x)) & 0x01) dma_display->drawPixel(x_offset + x, 16 + y, TEXT_COLOR); 
    }
  }
}

void drawColon(int x_offset) {
  dma_display->drawRect(x_offset + 1, 10, 2, 2, TEXT_COLOR);
  dma_display->drawRect(x_offset + 1, 20, 2, 2, TEXT_COLOR);
}

void showTimeOnMatrix(int h, int m) {
  dma_display->fillScreen(0); 
  drawDigit(1, h / 10);
  drawDigit(16, h % 10);
  drawColon(30);
  drawDigit(34, m / 10);
  drawDigit(49, m % 10);
}

void showStatusCode(int code) {
  showTimeOnMatrix(code / 100, code % 100);
}

// ================= 4. 初始化与引脚修复 =================

void initDisplay() {
  if (dma_display != nullptr) {
      delete dma_display;
      dma_display = nullptr;
  }

  HUB75_I2S_CFG mxconfig(PANEL_RES_X, PANEL_RES_Y, PANEL_CHAIN);
  mxconfig.gpio.r1 = R1_PIN; mxconfig.gpio.g1 = G1_PIN; mxconfig.gpio.b1 = B1_PIN;
  mxconfig.gpio.r2 = R2_PIN; mxconfig.gpio.g2 = G2_PIN; mxconfig.gpio.b2 = B2_PIN;
  mxconfig.gpio.a = A_PIN; mxconfig.gpio.b = B_PIN; mxconfig.gpio.c = C_PIN; mxconfig.gpio.d = D_PIN;
  mxconfig.gpio.e = E_PIN;
  mxconfig.gpio.lat = LAT_PIN; mxconfig.gpio.oe = OE_PIN; mxconfig.gpio.clk = CLK_PIN;
  
  // 保持 10MHz 稳定性
  //mxconfig.clk_speed = HUB75_I2S_CFG::HZ_10M; 

  dma_display = new MatrixPanel_I2S_DMA(mxconfig);
  dma_display->begin();
  dma_display->setBrightness8(128); 
  dma_display->fillScreen(0);
}

void fixGPIO25() {
    // 强制关闭 DAC，防止 WiFi 启动时将 GPIO25 劫持为模拟引脚
    dac_output_disable(DAC_CHANNEL_1);
    pinMode(25, OUTPUT);
}

// ================= 5. WiFi与文件系统 =================

bool loadWiFiConfig() {
  if (!SPIFFS.exists(wifiConfigPath)) return false;
  File file = SPIFFS.open(wifiConfigPath, FILE_READ);
  if (!file) return false;
  DynamicJsonDocument doc(512);
  DeserializationError error = deserializeJson(doc, file);
  file.close();
  if (error) return false;
  wifiSSID = doc["ssid"].as<String>();
  wifiPassword = doc["password"].as<String>();
  return !wifiSSID.isEmpty();
}

bool saveWiFiConfig(String ssid, String password) {
  DynamicJsonDocument doc(512);
  doc["ssid"] = ssid;
  doc["password"] = password;
  File file = SPIFFS.open(wifiConfigPath, FILE_WRITE);
  if (!file) return false;
  serializeJson(doc, file);
  file.close();
  wifiSSID = ssid;
  wifiPassword = password;
  return true;
}

void connectWiFiWithFix() {
  if (wifiSSID.isEmpty()) return;
  
  Serial.printf("\nConnecting WiFi: %s\n", wifiSSID.c_str());
  
  // 【重要】防止屏幕乱码
  WiFi.setSleep(false); 
  WiFi.mode(WIFI_STA);
  WiFi.begin(wifiSSID.c_str(), wifiPassword.c_str());
  
  unsigned long startWait = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startWait < 15000) {
    delay(500); Serial.print(".");
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nConnected! IP: %s\n", WiFi.localIP().toString().c_str());
    wifiConnected = true;
    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  } else {
    Serial.println("\nWiFi Failed");
    wifiConnected = false;
    WiFi.disconnect();
  }

  // 修复屏幕 (WiFi 启动可能会重置 GPIO 25)
  Serial.println("Re-initializing Display...");
  fixGPIO25();
  initDisplay(); 
  
  if (wifiConnected) showStatusCode(8888); else showStatusCode(0000);
  delay(1000);
  dma_display->fillScreen(0);
}

// ================= 6. CSV数据处理 (核心排查点) =================

void loadFromCSV() {
  countdowns.clear();
  if (!SPIFFS.exists(csvPath)) return;

  File f = SPIFFS.open(csvPath, FILE_READ);
  if (!f) return;

  // 获取当前时间，用于计算当天的绝对时间戳
  time_t now; time(&now);
  struct tm ti; 
  localtime_r(&now, &ti); // 获取当前年月日

  // 如果时间还没同步(1970年)，计算会出错
  if (ti.tm_year + 1900 < 2020) {
      Serial.println("Time not synced yet, skipping CSV load.");
      f.close();
      return;
  }

  int ln = 0;
  int id = 1;
  while (f.available()) {
    String line = f.readStringUntil('\n');
    line.trim();
    if (line.isEmpty() || ln == 0) { ln++; continue; } // 跳过表头
    
    // 格式: ID,HH:MM,Duration
    int c1 = line.indexOf(',');
    int c2 = line.indexOf(',', c1 + 1);
    if (c1 > 0 && c2 > 0) {
      String tStr = line.substring(c1 + 1, c2); // HH:MM
      String dStr = line.substring(c2 + 1);     // Duration
      
      int colon = tStr.indexOf(':');
      if (colon > 0) {
        int h = tStr.substring(0, colon).toInt();
        int m = tStr.substring(colon + 1).toInt();
        int dur = dStr.toInt();
        
        // 构建【今天】的开始时间戳
        struct tm st = ti;
        st.tm_hour = h; 
        st.tm_min = m; 
        st.tm_sec = 0;
        time_t startTime = mktime(&st);
        
        // 调试信息：打印解析出的时间
        Serial.printf("Task %d: %02d:%02d (%d min) -> Unix: %ld\n", id, h, m, dur, (long)startTime);

        Countdown cd;
        cd.id = id++;
        cd.startTime = startTime;
        cd.durationSeconds = dur * 60;
        cd.isActive = false;
        cd.isCompleted = false;
        cd.endTime = startTime + cd.durationSeconds;
        cd.debugTime = tStr;
        countdowns.push_back(cd);
      }
    }
    ln++;
  }
  f.close();
  Serial.printf("Total Loaded: %d tasks\n", countdowns.size());
}

// 接收串口 CSV 数据
void receiveAndSaveCSV() {
  File f = SPIFFS.open(csvPath, FILE_WRITE);
  if (!f) return;
  f.println("ID,Time,Dur");
  int id = 1;
  unsigned long start = millis();
  
  while (millis() - start < 5000) { // 5秒超时防止死锁
    if (Serial.available()) {
      String l = Serial.readStringUntil('\n');
      l.trim();
      if (l == "EOF") break;
      int sp = l.indexOf(' ');
      if (sp != -1) {
        f.printf("%d,%s,%s\n", id++, l.substring(0, sp).c_str(), l.substring(sp + 1).c_str());
        start = millis(); // 收到数据重置超时
      }
    }
  }
  f.close();
  Serial.println("CSV Saved.");
  loadFromCSV(); // 立即重新加载
}

// ================= 7. 运行逻辑 =================

void runClockMode() {
  static unsigned long last = 0;
  if (millis() - last >= 1000) {
    last = millis();
    time_t now; time(&now);
    struct tm ti;
    if (localtime_r(&now, &ti)) {
      showTimeOnMatrix(ti.tm_hour, ti.tm_min);
    }
  }
}

void runCountdownMode() {
  // 如果没有任务，或者时间没同步
  if (countdowns.empty()) { 
      static bool cleared = false;
      if(!cleared) { dma_display->fillScreen(0); cleared = true; Serial.println("No tasks or time not synced"); }
      return; 
  }
  
  time_t now; time(&now);
  bool anyActive = false;
  
  for (auto &cd : countdowns) {
    // 如果已经完成，跳过
    if (cd.isCompleted) continue;
    
    // 触发逻辑：当前时间 >= 开始时间 且 < 结束时间
    // 且任务之前未被激活
    if (!cd.isActive && now >= cd.startTime && now < cd.endTime) {
      cd.isActive = true;
      Serial.printf(">>> Task %s Started! <<<\n", cd.debugTime.c_str());
    }
    
    if (cd.isActive) {
      anyActive = true;
      long rem = (long)difftime(cd.endTime, now);
      
      if (rem <= 0) {
        cd.isActive = false;
        cd.isCompleted = true;
        Serial.printf(">>> Task %s Finished! <<<\n", cd.debugTime.c_str());
        showTimeOnMatrix(0, 0);
        delay(2000);
        dma_display->fillScreen(0);
      } else {
        // 显示倒计时
        static long lastRem = -1;
        if (rem != lastRem) { // 只有秒数变化才刷新，减少闪烁
            int m = rem / 60;
            int s = rem % 60;
            showTimeOnMatrix(m, s);
            // 调试打印 (每10秒打印一次证明还在活)
            if (rem % 10 == 0) Serial.printf("Countdown: %02d:%02d\n", m, s);
            lastRem = rem;
        }
      }
      break; // 同一时间只显示一个
    }
  }
  
  // 如果当前没有任何任务在运行
  if (!anyActive) {
      static bool cleared = false;
      // 这里的清屏会导致“黑屏等待”效果
      if(!cleared) { 
          dma_display->fillScreen(0); 
          cleared = true; 
          Serial.println("Waiting for next task...");
      }
  }
}

// ================= 8. Setup & Loop =================

void setup() {
  Serial.begin(115200);
  
  if (!SPIFFS.begin(true)) { Serial.println("SPIFFS Fail"); return; }

  if (loadWiFiConfig()) {
    connectWiFiWithFix(); 
  } else {
    Serial.println("No WiFi Config");
    fixGPIO25();
    initDisplay();
    showStatusCode(7777); 
  }
  
  // 上电后尝试加载一次 (通常此时还没时间，会失败，靠 Loop 里的重试)
  loadFromCSV();
}

void loop() {
  // A. 串口指令处理
  if (Serial.available()) {
    String c = Serial.readStringUntil('\n'); c.trim();
    if (c.startsWith("setwifi:")) {
      String p = c.substring(8);
      int cm = p.indexOf(',');
      if (cm != -1) {
         if (saveWiFiConfig(p.substring(0, cm), p.substring(cm+1))) {
             connectWiFiWithFix();
         }
      }
    } 
    else if (c == "getwifi") {
      Serial.printf("IP:%s\n", wifiSSID.c_str(), WiFi.localIP().toString().c_str()); 
    } 
    else if (c.startsWith("sendcsv")) {
      receiveAndSaveCSV();
    } 
    else if (c == "loadcsv") {
      loadFromCSV(); // 手动重载指令
    } 
    else if (c == "switchmode") {
      currentMode = (currentMode == CLOCK_MODE) ? COUNTDOWN_MODE : CLOCK_MODE;
      dma_display->fillScreen(0);
      Serial.printf("Mode Switched to: %s\n", currentMode == CLOCK_MODE ? "CLOCK" : "COUNTDOWN");
    }
  }

  // B. WiFi 断线重连
  static unsigned long lastW = 0;
  if (wifiConnected && millis() - lastW > 60000) { 
    lastW = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi Lost...");
      connectWiFiWithFix(); 
    }
  }

  // C. 时间同步监测与数据重载
  time_t now; time(&now);
  struct tm ti; localtime_r(&now, &ti);
  
  // 如果年份 < 2020，说明 NTP 还没通过
  if (ti.tm_year + 1900 < 2020) {
    static unsigned long b = 0;
    static bool tog;
    // 闪烁 00:00 提示
    if (millis() - b > 500) { b = millis(); tog = !tog; if(tog) showStatusCode(0); else dma_display->fillScreen(0); }
    
    // 标记未加载
    static bool timeSynced = false;
    timeSynced = false; 
    return;
  } 
  else {
      // 时间已同步
      static bool hasLoadedAfterSync = false;
      // 如果这是第一次检测到时间同步成功，必须重新加载 CSV！
      // 因为之前的 loadFromCSV 计算的时间戳是基于 1970 年的，全是错误的
      if (!hasLoadedAfterSync) {
          Serial.println("Time synced! Reloading CSV with correct date...");
          loadFromCSV();
          hasLoadedAfterSync = true;
          dma_display->fillScreen(0);
      }
  }

  // D. 运行模式
  if (currentMode == CLOCK_MODE) {
      runClockMode();
  } else {
      runCountdownMode();
  }
}
