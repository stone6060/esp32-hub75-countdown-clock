import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import csv
import serial
import serial.tools.list_ports
import re
import os
import time
from datetime import datetime

class ScheduleItem:
    def __init__(self, start_md="01-01", end_md="12-31", tasks=None):
        self.start_md = start_md
        self.end_md = end_md
        self.tasks = tasks if tasks else [] 

    def md_to_int(self, md):
        try:
            m, d = map(int, md.split('-'))
            return m * 100 + d
        except: return 0

    def is_overlap(self, other_start, other_end):
        s1 = self.md_to_int(self.start_md)
        e1 = self.md_to_int(self.end_md)
        s2 = self.md_to_int(other_start)
        e2 = self.md_to_int(other_end)
        if s1 > e1: e1 = 1231 
        if s2 > e2: e2 = 1231
        return not (e1 < s2 or s1 > e2)

class CountdownEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 时钟控制台")
        self.root.geometry("780x680")
        
        self.schedules = [] 
        self.current_idx = -1
        self.ser = None
        self.serial_connected = False
        self.wifi_csv_path = "wifi_config.csv"
        self.data_csv_path = "countdown_data.csv"
        
        self.setup_ui()
        self.update_serial_ports()
        self.load_local_data()
        self.load_wifi_config()

    def setup_ui(self):
        # === 1. 硬件连接 ===
        frame_hw = ttk.LabelFrame(self.root, text="硬件连接与控制", padding=10)
        frame_hw.pack(fill=tk.X, padx=10, pady=5)
        
        f_row1 = ttk.Frame(frame_hw)
        f_row1.pack(fill=tk.X)
        ttk.Label(f_row1, text="端口:").pack(side=tk.LEFT)
        self.cb_serial = ttk.Combobox(f_row1, width=10, state="readonly")
        self.cb_serial.pack(side=tk.LEFT, padx=5)
        ttk.Button(f_row1, text="刷新", width=6, command=self.update_serial_ports).pack(side=tk.LEFT)
        self.btn_connect = ttk.Button(f_row1, text="连接串口", width=10, command=self.toggle_serial)
        self.btn_connect.pack(side=tk.LEFT, padx=5)
        self.lbl_status = ttk.Label(f_row1, text="未连接", foreground="red")
        self.lbl_status.pack(side=tk.LEFT, padx=10)
        
        f_row2 = ttk.Frame(frame_hw)
        f_row2.pack(fill=tk.X, pady=(5,0))
        ttk.Label(f_row2, text="WiFi:").pack(side=tk.LEFT)
        self.ent_ssid = ttk.Entry(f_row2, width=12); self.ent_ssid.pack(side=tk.LEFT, padx=2)
        self.ent_pwd = ttk.Entry(f_row2, width=12, show="*"); self.ent_pwd.pack(side=tk.LEFT, padx=2)
        self.btn_set_wifi = ttk.Button(f_row2, text="写入WiFi", command=self.set_wifi, state="disabled")
        self.btn_set_wifi.pack(side=tk.LEFT, padx=5)
        self.btn_mode = ttk.Button(f_row2, text="切换模式 (时钟/倒计时)", command=self.switch_mode, state="disabled")
        self.btn_mode.pack(side=tk.RIGHT, padx=5)

        # === 2. 日期区间管理 ===
        frame_range = ttk.LabelFrame(self.root, text="日期区间管理", padding=10)
        frame_range.pack(fill=tk.X, padx=10, pady=5)
        
        f_r1 = ttk.Frame(frame_range)
        f_r1.pack(fill=tk.X)
        ttk.Label(f_r1, text="选择区间:").pack(side=tk.LEFT)
        self.cb_ranges = ttk.Combobox(f_r1, state="readonly", width=30)
        self.cb_ranges.pack(side=tk.LEFT, padx=5)
        self.cb_ranges.bind("<<ComboboxSelected>>", self.on_range_selected)
        ttk.Button(f_r1, text="➕ 新建", width=10, command=self.add_range).pack(side=tk.LEFT, padx=5)
        ttk.Button(f_r1, text="🗑 删除", width=8, command=self.del_range).pack(side=tk.LEFT, padx=2)
        ttk.Button(f_r1, text="💾 保存数据", width=14, command=self.save_local_data).pack(side=tk.RIGHT)

        f_r2 = ttk.Frame(frame_range)
        f_r2.pack(fill=tk.X, pady=(5,0))
        ttk.Label(f_r2, text="修改日期:  ").pack(side=tk.LEFT)
        self.ent_start_md = ttk.Entry(f_r2, width=8); self.ent_start_md.pack(side=tk.LEFT, padx=2)
        ttk.Label(f_r2, text=" 至 ").pack(side=tk.LEFT)
        self.ent_end_md = ttk.Entry(f_r2, width=8); self.ent_end_md.pack(side=tk.LEFT, padx=2)
        ttk.Button(f_r2, text="更新", width=6, command=self.update_range_date).pack(side=tk.LEFT, padx=10)

        # === 3. 任务列表 ===
        frame_task = ttk.Frame(self.root, padding=10)
        frame_task.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame_task, columns=("s","d","e"), show="headings", selectmode="browse")
        self.tree.heading("s", text="开始时间"); self.tree.column("s", anchor="center")
        self.tree.heading("d", text="时长 (分)"); self.tree.column("d", anchor="center")
        self.tree.heading("e", text="结束时间"); self.tree.column("e", anchor="center")
        sc = ttk.Scrollbar(frame_task, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sc.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Double-1>", self.edit_task_dialog)

        # === 4. 底部 ===
        frame_btm = ttk.Frame(self.root, padding=10)
        frame_btm.pack(fill=tk.X)
        ttk.Label(frame_btm, text="时间:").pack(side=tk.LEFT)
        self.ent_time = ttk.Entry(frame_btm, width=8); self.ent_time.pack(side=tk.LEFT, padx=5)
        self.ent_time.insert(0, "08:00")
        ttk.Label(frame_btm, text="时长:").pack(side=tk.LEFT)
        self.ent_dur = ttk.Entry(frame_btm, width=6); self.ent_dur.pack(side=tk.LEFT, padx=5)
        self.ent_dur.insert(0, "45")
        ttk.Button(frame_btm, text="添加任务", command=self.add_task).pack(side=tk.LEFT, padx=10)
        
        self.btn_sync = tk.Button(frame_btm, text="⚡ 同步到设备 (Serial)", bg="#e1f5fe", command=self.sync_via_serial, state="disabled")
        self.btn_sync.pack(side=tk.RIGHT, ipadx=10)

    # ================= 业务逻辑 =================
    def validate_date_format(self, d):
        try: datetime.strptime(f"2024-{d}", "%Y-%m-%d"); return True
        except: return False
    def validate_time_format(self, t): return bool(re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', t))
    def time_to_min(self, t): h,m=map(int,t.split(':')); return h*60+m
    
    def check_task_overlap(self, tasks, new_t, new_d, ignore=-1):
        s_n = self.time_to_min(new_t); e_n = s_n + new_d
        for i, t in enumerate(tasks):
            if i==ignore: continue
            s = self.time_to_min(t[1]); e = s + t[2]
            if max(s_n, s) < min(e_n, e): return True, f"冲突: {t[1]}"
        return False, ""
        
    def check_range_overlap(self, s, e, ignore=-1):
        tmp = ScheduleItem(s, e)
        for i, it in enumerate(self.schedules):
            if i==ignore: continue
            if tmp.is_overlap(it.start_md, it.end_md): return True, f"冲突: {it.start_md}"
        return False, ""

    def refresh_ui(self):
        vals = [f"{i.start_md} ~ {i.end_md}" for i in self.schedules]
        self.cb_ranges['values'] = vals
        if self.current_idx >= 0 and self.current_idx < len(self.schedules):
            self.cb_ranges.current(self.current_idx)
            c = self.schedules[self.current_idx]
            self.ent_start_md.delete(0, tk.END); self.ent_start_md.insert(0, c.start_md)
            self.ent_end_md.delete(0, tk.END); self.ent_end_md.insert(0, c.end_md)
            self.refresh_task_tree()
        else:
            self.cb_ranges.set(""); self.tree.delete(*self.tree.get_children())

    def refresh_task_tree(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        if self.current_idx < 0: return
        sch = self.schedules[self.current_idx]
        sch.tasks.sort(key=lambda x: self.time_to_min(x[1]))
        for t in sch.tasks:
            tot = self.time_to_min(t[1]) + t[2]
            h,m = (tot//60)%24, tot%60
            es = f"{h:02d}:{m:02d}" + (" (+1)" if tot>=1440 else "")
            self.tree.insert("", tk.END, values=(t[1], t[2], es))

    def on_range_selected(self, e):
        i = self.cb_ranges.current()
        if i>=0: self.current_idx=i; self.refresh_ui()

    def add_range(self):
        s = simpledialog.askstring("新区间","开始 (MM-DD):", parent=self.root)
        if not s: return
        e = simpledialog.askstring("新区间","结束 (MM-DD):", parent=self.root)
        if not e: return
        if not self.validate_date_format(s) or not self.validate_date_format(e):
            messagebox.showerror("错误","格式错误"); return
        ov, m = self.check_range_overlap(s, e)
        if ov: messagebox.showerror("错误", m); return
        self.schedules.append(ScheduleItem(s, e))
        self.current_idx = len(self.schedules)-1
        self.refresh_ui()

    def update_range_date(self):
        if self.current_idx < 0: return
        s = self.ent_start_md.get().strip(); e = self.ent_end_md.get().strip()
        if not self.validate_date_format(s): return
        ov, m = self.check_range_overlap(s, e, self.current_idx)
        if ov: messagebox.showerror("错误", m); return
        self.schedules[self.current_idx].start_md = s
        self.schedules[self.current_idx].end_md = e
        self.refresh_ui()

    def del_range(self):
        if self.current_idx < 0: return
        if messagebox.askyesno("确认","删除?"):
            del self.schedules[self.current_idx]
            self.current_idx = 0 if self.schedules else -1
            self.refresh_ui()

    def add_task(self):
        if self.current_idx < 0: return
        t = self.ent_time.get().strip()
        try: d = int(self.ent_dur.get().strip())
        except: return
        if not self.validate_time_format(t): return
        ts = self.schedules[self.current_idx].tasks
        ov, m = self.check_task_overlap(ts, t, d)
        if ov: messagebox.showerror("错误", m); return
        self.schedules[self.current_idx].tasks.append((0, t, d))
        self.refresh_task_tree()

    def show_context_menu(self, e):
        if not self.tree.identify_row(e.y): return
        self.tree.selection_set(self.tree.identify_row(e.y))
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="编辑", command=self.edit_task_dialog)
        m.add_command(label="删除", command=self.del_task)
        m.post(e.x_root, e.y_root)

    def del_task(self):
        sel = self.tree.selection()
        if not sel: return
        v = self.tree.item(sel[0], "values")
        ts = self.schedules[self.current_idx].tasks
        for i, t in enumerate(ts):
            if t[1]==v[0] and str(t[2])==str(v[1]): del ts[i]; break
        self.refresh_task_tree()

    # 【核心修复】独立的编辑窗口，不误删数据
    def edit_task_dialog(self, e=None):
        sel = self.tree.selection()
        if not sel: return
        
        # 获取索引和数据
        tree_idx = self.tree.index(sel[0])
        tasks = self.schedules[self.current_idx].tasks
        if tree_idx >= len(tasks): return
        
        curr = tasks[tree_idx]
        
        # 弹窗
        win = tk.Toplevel(self.root)
        win.title("修改任务")
        win.geometry("300x200")
        win.resizable(False,False)
        win.transient(self.root)
        win.grab_set()
        
        x = self.root.winfo_x() + (self.root.winfo_width()//2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height()//2) - 90
        win.geometry(f"+{x}+{y}")

        ttk.Label(win, text="开始时间 (HH:MM):").pack(pady=(20,5))
        e_t = ttk.Entry(win, width=15, justify="center"); e_t.pack(); e_t.insert(0, curr[1])
        ttk.Label(win, text="时长 (分钟):").pack(pady=(10,5))
        e_d = ttk.Entry(win, width=15, justify="center"); e_d.pack(); e_d.insert(0, str(curr[2]))

        def confirm():
            nt = e_t.get().strip(); nds = e_d.get().strip()
            if not self.validate_time_format(nt): messagebox.showerror("错误","时间格式错误", parent=win); return
            try: nd = int(nds); 
            except: messagebox.showerror("错误","时长错误", parent=win); return
            if nd<=0: messagebox.showerror("错误","时长需>0", parent=win); return
            
            # 重叠校验 (忽略自己)
            ov, m = self.check_task_overlap(tasks, nt, nd, ignore=tree_idx)
            if ov: messagebox.showerror("冲突", m, parent=win); return
            
            tasks[tree_idx] = (0, nt, nd)
            self.refresh_task_tree()
            win.destroy()
            messagebox.showinfo("成功", "已保存")

        ttk.Button(win, text="保存", command=confirm).pack(pady=20)

    # ================= 硬件通信 =================
    
    def update_serial_ports(self):
        self.cb_serial['values'] = [p.device for p in serial.tools.list_ports.comports()]
        if self.cb_serial['values']: self.cb_serial.current(0)

    def toggle_serial(self):
        if self.serial_connected:
            if self.ser:
                try: self.ser.dtr=False; self.ser.rts=False; self.ser.close()
                except: pass
            self.serial_connected = False
            self.btn_connect.config(text="打开串口")
            self.lbl_status.config(text="未连接", foreground="red")
            self.btn_set_wifi.config(state="disabled")
            self.btn_mode.config(state="disabled")
            self.btn_sync.config(state="disabled")
        else:
            p = self.cb_serial.get()
            if not p: return
            try:
                self.ser = serial.Serial()
                self.ser.port = p
                self.ser.baudrate = 115200
                self.ser.timeout = 1
                self.ser.dtr = False; self.ser.rts = False
                self.ser.open()
                self.serial_connected = True
                self.btn_connect.config(text="断开串口")
                self.lbl_status.config(text="已连接", foreground="green")
                self.btn_set_wifi.config(state="normal")
                self.btn_mode.config(state="normal")
                self.btn_sync.config(state="normal")
            except Exception as e: messagebox.showerror("失败", str(e))

    def set_wifi(self):
        if not self.serial_connected: return
        s = self.ent_ssid.get().strip(); p = self.ent_pwd.get().strip()
        if not s: return
        try:
            self.ser.write(f"setwifi:{s},{p}\n".encode())
            messagebox.showinfo("成功", "WiFi配置已发送")
            with open(self.wifi_csv_path, 'w', encoding='utf-8') as f: f.write(f"{s}\n{p}")
        except Exception as e: messagebox.showerror("错误", str(e))

    def switch_mode(self):
        if self.serial_connected:
            try: 
                self.ser.write(b"switchmode\n")
                messagebox.showinfo("成功", "模式切换指令已发送")
            except Exception as e: messagebox.showerror("错误", str(e))

    def sync_via_serial(self):
        if not self.serial_connected: return
        if self.current_idx < 0: return
        tasks = self.schedules[self.current_idx].tasks
        if not tasks: messagebox.showwarning("空", "无任务"); return
        try:
            self.ser.write(b"sendcsv\n"); time.sleep(0.2)
            for t in tasks:
                self.ser.write(f"{t[1]} {t[2]}\n".encode()); time.sleep(0.05)
            self.ser.write(b"EOF\n")
            messagebox.showinfo("成功", "同步完成")
        except Exception as e: messagebox.showerror("错误", str(e))

    # ================= 存取 =================
    def save_local_data(self):
        try:
            with open(self.data_csv_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                for i in self.schedules:
                    w.writerow([f"RANGE~{i.start_md}~{i.end_md}"])
                    w.writerow(["ID","T","D"])
                    for idx,t in enumerate(i.tasks): w.writerow([idx,t[1],t[2]])
                    w.writerow([])
            messagebox.showinfo("成功", "保存成功")
        except: pass

    def load_local_data(self):
        if not os.path.exists(self.data_csv_path): return
        try:
            self.schedules = []; curr = None
            with open(self.data_csv_path, 'r', encoding='utf-8') as f:
                r = csv.reader(f)
                for row in r:
                    if not row: continue
                    if '~' in row[0]:
                        p = row[0].split('~')
                        curr = ScheduleItem(p[-2], p[-1])
                        self.schedules.append(curr)
                    elif len(row)>=3 and row[0].isdigit() and curr:
                        curr.tasks.append((0, row[1], int(row[2])))
            self.refresh_ui()
        except: pass

    def load_wifi_config(self):
        if os.path.exists(self.wifi_csv_path):
            try:
                with open(self.wifi_csv_path,'r',encoding='utf-8') as f:
                    l = f.readlines()
                    if len(l)>=2: self.ent_ssid.insert(0,l[0].strip()); self.ent_pwd.insert(0,l[1].strip())
            except: pass

if __name__ == "__main__":
    root = tk.Tk()
    try: from ctypes import windll; windll.shcore.SetProcessDpiAwareness(1)
    except: pass
    app = CountdownEditor(root)
    root.mainloop()
