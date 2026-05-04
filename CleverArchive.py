print("🚀 STARTED!", flush=True)
import os
import sys

# --- FIX FOR GIANT PDFs ---
sys.setrecursionlimit(5000)

# ==============================================================================
# 1. ANTI-CRASH BOOTSTRAP FOR NETWORK DRIVES (SMB/UNC)
# ==============================================================================
if sys.platform == 'win32':
    try:
        local_folder = os.environ.get('USERPROFILE', 'C:\\')
        os.chdir(local_folder)
    except Exception:
        pass
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# ==============================================================================
# 2. STANDARD IMPORTS
# ==============================================================================
import ctypes
import json
import re
import time
import sqlite3
import base64
import threading
import tempfile
import subprocess
import numpy as np
import concurrent.futures
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk, simpledialog
from pathlib import Path

from google import genai
from google.genai import types

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CleverArchive")
except Exception:
    pass


def hide_folder(path):
    if sys.platform == 'win32':
        try:
            FILE_ATTRIBUTE_HIDDEN = 0x02
            path_str = str(path)
            current_attributes = ctypes.windll.kernel32.GetFileAttributesW(path_str)
            if current_attributes != -1:
                ctypes.windll.kernel32.SetFileAttributesW(path_str, current_attributes | FILE_ATTRIBUTE_HIDDEN)
        except Exception:
            pass


print("[1] Libraries imported. Starting global space read...")

# --- PATH DEFINITIONS ---
APP_NAME = "CleverArchive"

# 1. NETWORK Path
NETWORK_DIR = Path(r'\\192.168.200.160\dfs-dati\daticon\METER\CONFIG CLEVER ARCHIVE')
try:
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    hide_folder(NETWORK_DIR)
except Exception as e:
    print(f"⚠️ Unable to reach network path: {e}")

NETWORK_CONFIG_PATH = NETWORK_DIR / "ai_credentials.json"

# 2. LOCAL Path
appdata_folder = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
LOCAL_DIR = Path(appdata_folder) / APP_NAME
LOCAL_DIR.mkdir(parents=True, exist_ok=True)
hide_folder(LOCAL_DIR)
LOCAL_CONFIG_PATH = LOCAL_DIR / "ai_local_config.json"


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def get_db_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def safe_relative_path(path, start):
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return os.path.relpath(os.path.realpath(path), os.path.realpath(start))


# ==========================================
# 3. SECURITY AND NATIVE CRYPTOGRAPHY
# ==========================================
def encrypt_key(api_key, password):
    api_bytes = api_key.encode('utf-8')
    pwd_bytes = password.encode('utf-8')
    xor_bytes = bytearray(b ^ pwd_bytes[i % len(pwd_bytes)] for i, b in enumerate(api_bytes))
    return base64.b64encode(xor_bytes).decode('utf-8')


def decrypt_key(encrypted_key, password):
    try:
        xor_bytes = base64.b64decode(encrypted_key)
        pwd_bytes = password.encode('utf-8')
        api_bytes = bytearray(b ^ pwd_bytes[i % len(pwd_bytes)] for i, b in enumerate(xor_bytes))
        return api_bytes.decode('utf-8')
    except Exception:
        return ""


# ==========================================
# 4. GLOBAL CONFIGURATIONS
# ==========================================
DEFAULT_API_KEY = ""
DEFAULT_PASSWORD = ""
SYSTEM_SECRET_KEY = ""

ACCESS_PASSWORD = DEFAULT_PASSWORD
API_KEY = DEFAULT_API_KEY

SAVED_DB_PATH = "No file selected"
SAVED_FOLDER_PATH = "No directory selected"
SAVED_USE_SYNTHESIS = True

SELECTED_EXTRACTION_FOLDERS = set()
SAVED_API_RATES = {}


def update_config(key, value):
    network_keys = ["geminiapi", "app_pwd"]
    destination_path = NETWORK_CONFIG_PATH if key in network_keys else LOCAL_CONFIG_PATH

    current_config = {}
    if destination_path.exists():
        try:
            with open(destination_path, "r", encoding="utf-8") as f:
                current_config = json.load(f)
        except Exception:
            pass

    if key == "extraction_folders" and isinstance(value, set):
        value = list(value)

    current_config[key] = value

    try:
        with open(destination_path, "w", encoding="utf-8") as f:
            json.dump(current_config, f, indent=4)
    except PermissionError:
        print(f"⚠️ Permissions error on: {destination_path}")
    except Exception as e:
        print(f"⚠️ Save error: {e}")


if LOCAL_CONFIG_PATH.exists():
    try:
        with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            c_loc = json.load(f)
            SAVED_DB_PATH = c_loc.get("db_path", "No file selected")
            SAVED_FOLDER_PATH = c_loc.get("folder_path", "No directory selected")
            SAVED_USE_SYNTHESIS = c_loc.get("use_synthesis", True)
            SELECTED_EXTRACTION_FOLDERS = set(c_loc.get("extraction_folders", []))
            SAVED_API_RATES = c_loc.get("api_rates", {})
    except Exception:
        pass

# --- OPERATIONAL PARAMETERS ---
NUMBER_RELATED_DOCS = 10
RELEVANCE_THRESHOLD = 0.50
MAX_PAGES_PER_BLOCK = 4


def calculate_optimal_workers():
    try:
        available_cores = os.cpu_count() or 4
        workers = min(10, int(available_cores * 1.5))
        return workers
    except Exception:
        return 3


MAX_WORKERS = calculate_optimal_workers()

EXTRACTION_PROMPT = """
Extract data from this technical document. The document might have complex layouts (e.g., dual-column) and be in English, Italian, or multilingual.

EXTRACTION RULES:
1. Extract the "macro_title".
2. Subdivide into sections based on titles.
3. Extract the "text" entirely. If the page has multiple columns, follow the STRICT logical reading order (read the entire left column before moving to the right one).
4. MAINTAIN THE ORIGINAL LANGUAGE of the text. Do not translate technical terms.
5. Rejoin hyphenated words at the end of lines and texts interrupted by page breaks.
6. REPLACE ALL double quotes (") within the text with single quotes ('). This is VITAL to avoid corrupting the JSON structure.
7. Ignore revision tables, signatures, and dates.

RETURN EXCLUSIVELY A JSON ARRAY WITH THIS STRUCTURE:
[{"macro_title": "...", "section": "...", "text": "..."}]
"""


def cosine_similarity(vec1, vec2):
    v1 = np.array(vec1)
    v2 = np.array(vec2)
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))


# ==========================================
# CUSTOM WIDGET: FOLDER TREE (EXTRACTION)
# ==========================================
class CheckboxFolderTree(ttk.Treeview):
    def __init__(self, master, root_dir, checked_set, **kwargs):
        super().__init__(master, **kwargs)
        self.root_dir = root_dir
        self.checked_set = checked_set

        self.heading('#0', text=' Explore Folders', anchor='w')

        self.bind('<Button-1>', self.on_single_click)
        self.bind('<Double-1>', self.on_double_click)
        self.bind('<<TreeviewOpen>>', self.on_expand)
        self.bind('<Button-3>', self.on_right_click)

        self.tag_configure("checked", foreground="#059669")
        self.tag_configure("unchecked", foreground="#6B7280")

        self.after(100, self.populate_root)

    def on_right_click(self, event):
        item = self.identify_row(event.y)
        if not item:
            return

        self.selection_set(item)
        self.focus(item)

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Expand all", command=lambda: self.expand_all(item))
        menu.add_command(label="Collapse all", command=lambda: self.collapse_all(item))
        menu.add_command(label="Collapse unselected", command=lambda: self.collapse_unselected(item))
        menu.add_separator()
        menu.add_command(label="Select all", command=lambda: self.check_all(item))
        menu.add_command(label="Deselect all", command=lambda: self.uncheck_all(item))

        menu.post(event.x_root, event.y_root)

    def _force_populate_recursive(self, item):
        children = self.get_children(item)
        if len(children) == 1 and self.item(children[0], "text") == "dummy":
            self.delete(children[0])
            path = self.item(item, "values")[0]
            self.populate_node(item, path)

        for child in self.get_children(item):
            self._force_populate_recursive(child)

    def expand_all(self, item):
        self._force_populate_recursive(item)
        self._set_open_recursive(item, True)

    def collapse_all(self, item):
        self._set_open_recursive(item, False)

    def _set_open_recursive(self, item, state):
        self.item(item, open=state)
        for child in self.get_children(item):
            self._set_open_recursive(child, state)

    def check_all(self, item):
        self._force_populate_recursive(item)
        self._check_recursive(item)
        self._check_parents_upward(item)
        self.event_generate("<<TreeChecked>>")

    def _check_recursive(self, item):
        text = self.item(item, "text")
        path = self.item(item, "values")[0]

        if text.startswith("☐ "):
            content = text[2:]
            self.item(item, text=f"☑ {content}", tags=("checked",))
            self.checked_set.add(path)

        for child in self.get_children(item):
            self._check_recursive(child)

    def uncheck_all(self, item):
        text = self.item(item, "text")
        path = self.item(item, "values")[0]

        if text.startswith("☑ "):
            content = text[2:]
            self.item(item, text=f"☐ {content}", tags=("unchecked",))
            self.checked_set.discard(path)

        paths_to_remove = [p for p in self.checked_set if p.startswith(path + os.sep)]
        for p in paths_to_remove:
            self.checked_set.discard(p)

        for child in self.get_children(item):
            self.delete(child)

        try:
            if any(os.path.isdir(os.path.join(path, sub)) for sub in os.listdir(path)):
                self.insert(item, "end", text="dummy")
        except Exception:
            pass

        self.item(item, open=False)
        self.event_generate("<<TreeChecked>>")

    def collapse_unselected(self, item):
        text = self.item(item, "text")
        if text.startswith("☐ "):
            self.item(item, open=False)
        else:
            for child in self.get_children(item):
                self.collapse_unselected(child)

    def count_pdfs(self, path):
        try:
            return len([f for f in os.listdir(path) if f.lower().endswith('.pdf')])
        except Exception:
            return 0

    def populate_root(self):
        for item in self.get_children():
            self.delete(item)

        if not self.root_dir or not os.path.exists(self.root_dir):
            return

        is_checked = self.root_dir in self.checked_set
        text_status = "☑ " if is_checked else "☐ "
        tag = "checked" if is_checked else "unchecked"

        n_pdf = self.count_pdfs(self.root_dir)
        label = f"{text_status}{os.path.basename(self.root_dir)} ({n_pdf})"

        root_node = self.insert('', 'end', text=label, values=(self.root_dir,), tags=(tag,))
        self.populate_node(root_node, self.root_dir)
        self.item(root_node, open=True)

    def populate_node(self, parent_node, parent_path):
        try:
            elements = sorted(os.listdir(parent_path))
            for element in elements:
                full_path = os.path.join(parent_path, element)
                if os.path.isdir(full_path):
                    is_checked = full_path in self.checked_set
                    text_status = "☑ " if is_checked else "☐ "
                    tag = "checked" if is_checked else "unchecked"

                    n_pdf = self.count_pdfs(full_path)
                    label = f"{text_status}{element} ({n_pdf})"

                    node_id = self.insert(parent_node, "end", text=label, values=(full_path,), tags=(tag,))

                    try:
                        if any(os.path.isdir(os.path.join(full_path, sub)) for sub in os.listdir(full_path)):
                            self.insert(node_id, "end", text="dummy")
                    except Exception:
                        pass
        except PermissionError:
            pass

    def on_expand(self, event):
        node = self.focus()
        children = self.get_children(node)
        if len(children) == 1 and self.item(children[0], "text") == "dummy":
            self.delete(children[0])
            full_path = self.item(node, "values")[0]
            self.populate_node(node, full_path)

    def on_single_click(self, event):
        if self.identify_element(event.x, event.y) != "text":
            return
        item = self.identify_row(event.y)
        if not item:
            return

        bbox = self.bbox(item, '#0')
        if not bbox:
            return
        x_start = bbox[0]

        if event.x <= x_start + 28:
            self.toggle_item(item)
            return "break"

    def on_double_click(self, event):
        if self.identify_element(event.x, event.y) != "text":
            return
        item = self.identify_row(event.y)
        if not item:
            return

        self.toggle_item(item)
        return "break"

    def toggle_item(self, item):
        text = self.item(item, "text")
        path = self.item(item, "values")[0]

        prefix = text[:2]
        content = text[2:]

        if prefix == "☐ ":
            new_text = f"☑ {content}"
            self.item(item, text=new_text, tags=("checked",))
            self.checked_set.add(path)
            self._check_parents_upward(item)
            self.item(item, open=True)

            children = self.get_children(item)
            if len(children) == 1 and self.item(children[0], "text") == "dummy":
                self.delete(children[0])
                self.populate_node(item, path)

        elif prefix == "☑ ":
            new_text = f"☐ {content}"
            self.item(item, text=new_text, tags=("unchecked",))
            self.checked_set.discard(path)

            paths_to_remove = [p for p in self.checked_set if p.startswith(path + os.sep)]
            for p in paths_to_remove:
                self.checked_set.discard(p)

            for child in self.get_children(item):
                self.delete(child)

            try:
                if any(os.path.isdir(os.path.join(path, sub)) for sub in os.listdir(path)):
                    self.insert(item, "end", text="dummy")
            except Exception:
                pass
            self.item(item, open=False)

        self.event_generate("<<TreeChecked>>")

    def _check_parents_upward(self, item):
        parent = self.parent(item)
        if parent:
            text = self.item(parent, "text")
            if text.startswith("☐ "):
                path = self.item(parent, "values")[0]
                content = text[2:]
                self.item(parent, text=f"☑ {content}", tags=("checked",))
                self.checked_set.add(path)
                self._check_parents_upward(parent)


# ==========================================
# 5. UNIFIED APPLICATION
# ==========================================
class AIGuideSystem:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Management System")

        try:
            self.root.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        window_width = 1250
        window_height = 900
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        self.root.minsize(1200, 750)
        self.root.update()

        self.bg_color = "#F9FAFB"
        self.card_color = "#FFFFFF"
        self.primary_color = "#1E3A8A"
        self.btn_color = "#2563EB"
        self.btn_bg_light = "#E5E7EB"
        self.btn_bg_green = "#34D399"
        self.btn_bg_blue = "#60A5FA"
        self.btn_bg_red = "#F87171"
        self.btn_bg_purple = "#A78BFA"

        self.root.configure(bg=self.bg_color)

        self.db_path = tk.StringVar(value=SAVED_DB_PATH)
        self.folder_path = tk.StringVar(value=SAVED_FOLDER_PATH)
        self.use_synthesis_var = tk.BooleanVar(value=SAVED_USE_SYNTHESIS)
        self.use_debug_var = tk.BooleanVar(value=False)
        self.api_rates = SAVED_API_RATES
        self.search_filter_state = {}

        self.stop_flag = threading.Event()
        self.lock_db = threading.Lock()

        self.client = None
        self.text_models = ['gemini-2.5-flash', 'gemini-1.5-flash']
        self.embed_model = 'gemini-embedding-2'

        self.setup_ui()

        threading.Thread(target=self._initialize_network_credentials_bg, daemon=True).start()
        self.root.update_idletasks()
        self.root.update_idletasks()

    def _initialize_network_credentials_bg(self):
        global ACCESS_PASSWORD, API_KEY

        if NETWORK_CONFIG_PATH.exists():
            try:
                with open(NETWORK_CONFIG_PATH, "r", encoding="utf-8") as f:
                    c_net = json.load(f)
                    saved_pwd = c_net.get("app_pwd")
                    if saved_pwd:
                        ACCESS_PASSWORD = base64.b64decode(saved_pwd).decode('utf-8')

                    encrypted_key = c_net.get("geminiapi")
                    if encrypted_key:
                        local_api = decrypt_key(encrypted_key, ACCESS_PASSWORD)
                        if local_api:
                            API_KEY = local_api
            except Exception as e:
                print(f"Error reading network credentials: {e}")

        if API_KEY:
            try:
                self.client = genai.Client(api_key=API_KEY)
                self.update_models_from_api()
            except Exception:
                pass

    def open_rates_popup(self):
        if not hasattr(self, 'available_flash_lists'):
            tk.messagebox.showwarning("Wait", "Please wait for initial models loading before setting rates.")
            return

        top = tk.Toplevel(self.root)
        top.title("AI Models Rates ($ per 1 Million Tokens)")

        popup_width = 700
        popup_height = 500
        screen_width = top.winfo_screenwidth()
        screen_height = top.winfo_screenheight()
        top.geometry(f"{popup_width}x{popup_height}+{int(screen_width / 2 - popup_width / 2)}+{int(screen_height / 2 - popup_height / 2)}")
        top.transient(self.root)
        top.grab_set()
        top.configure(bg="#F9FAFB")

        tk.Label(top, text="Set cost in $ per 1 Million Tokens:", font=("Segoe UI", 12, "bold"), bg="#F9FAFB").pack(pady=10)

        container = tk.Frame(top, bd=1, relief="solid", bg="#FFFFFF")
        container.pack(fill="both", expand=True, padx=20, pady=5)
        canvas = tk.Canvas(container, bg="#FFFFFF", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scroll_f = tk.Frame(canvas, bg="#FFFFFF")
        scroll_f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_f, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        header_f = tk.Frame(scroll_f, bg="#E5E7EB")
        header_f.pack(fill="x")
        tk.Label(header_f, text="API Model", font=("Segoe UI", 10, "bold"), width=22, anchor="w", bg="#E5E7EB").pack(side="left", padx=5, pady=2)
        tk.Label(header_f, text="IN (≤200K)", font=("Segoe UI", 9, "bold"), width=10, bg="#E5E7EB").pack(side="left", padx=2)
        tk.Label(header_f, text="OUT (≤200K)", font=("Segoe UI", 9, "bold"), width=11, bg="#E5E7EB").pack(side="left", padx=2)
        tk.Label(header_f, text="IN (>200K)", font=("Segoe UI", 9, "bold"), width=10, bg="#E5E7EB").pack(side="left", padx=2)
        tk.Label(header_f, text="OUT (>200K)", font=("Segoe UI", 9, "bold"), width=11, bg="#E5E7EB").pack(side="left", padx=2)

        all_models = set(self.available_flash_lists + self.available_pro_lists + self.available_embed_lists)
        all_models = sorted(list(all_models))

        self.rates_entries = {}

        for mod in all_models:
            row_f = tk.Frame(scroll_f, bg="#FFFFFF", pady=2)
            row_f.pack(fill="x", padx=5)

            tk.Label(row_f, text=mod, font=("Segoe UI", 10), width=22, anchor="w", bg="#FFFFFF").pack(side="left", padx=5)

            t = self.api_rates.get(mod, {})
            v_in_b = t.get("in_base", t.get("input", 0.0))
            v_out_b = t.get("out_base", t.get("output", 0.0))
            v_in_e = t.get("in_ext", t.get("input", 0.0))
            v_out_e = t.get("out_ext", t.get("output", 0.0))

            e_ib = tk.Entry(row_f, font=("Segoe UI", 10), width=10, justify="center")
            e_ib.insert(0, str(v_in_b))
            e_ib.pack(side="left", padx=2)

            e_ob = tk.Entry(row_f, font=("Segoe UI", 10), width=11, justify="center")
            e_ob.insert(0, str(v_out_b))
            e_ob.pack(side="left", padx=2)

            e_ie = tk.Entry(row_f, font=("Segoe UI", 10), width=10, justify="center")
            e_ie.insert(0, str(v_in_e))
            e_ie.pack(side="left", padx=2)

            e_oe = tk.Entry(row_f, font=("Segoe UI", 10), width=11, justify="center")
            e_oe.insert(0, str(v_out_e))
            e_oe.pack(side="left", padx=2)

            self.rates_entries[mod] = {"ib": e_ib, "ob": e_ob, "ie": e_ie, "oe": e_oe}

        def save_rates():
            new_rates = {}

            def clean_value(text):
                import re
                numbers_only = re.sub(r'[^\d,.]', '', text)
                if not numbers_only:
                    return 0.0
                try:
                    return float(numbers_only.replace(",", "."))
                except ValueError:
                    return 0.0

            for mod, ent in self.rates_entries.items():
                c_ib = clean_value(ent["ib"].get())
                c_ob = clean_value(ent["ob"].get())
                c_ie = clean_value(ent["ie"].get())
                c_oe = clean_value(ent["oe"].get())

                new_rates[mod] = {
                    "in_base": c_ib, "out_base": c_ob,
                    "in_ext": c_ie, "out_ext": c_oe
                }

            self.api_rates = new_rates
            update_config("api_rates", self.api_rates)

            messagebox.showinfo("Configuration", "Rates saved successfully.", parent=top)

            canvas.unbind_all("<MouseWheel>")
            top.destroy()

        tk.Button(top, text="Save Rates", bg=self.btn_bg_green, font=("Segoe UI", 11, "bold"),
                  command=save_rates).pack(pady=15, ipady=4, ipadx=20)

    def track_usage(self, action, used_model, api_response, custom_log_widget=None):
        if not self.use_debug_var.get():
            return "", 0.0

        try:
            in_tokens = getattr(api_response.usage_metadata, 'prompt_token_count', 0) if hasattr(api_response, 'usage_metadata') else 0
            out_tokens = getattr(api_response.usage_metadata, 'candidates_token_count', 0) if hasattr(api_response, 'usage_metadata') else 0

            TOKEN_THRESHOLD = 200000

            model_rates = self.api_rates.get(used_model, {})

            if in_tokens <= TOKEN_THRESHOLD:
                rate_in = model_rates.get("in_base", model_rates.get("input", 0.0))
                rate_out = model_rates.get("out_base", model_rates.get("output", 0.0))
            else:
                rate_in = model_rates.get("in_ext", model_rates.get("input", 0.0))
                rate_out = model_rates.get("out_ext", model_rates.get("output", 0.0))

            call_cost = (in_tokens / 1_000_000 * rate_in) + (out_tokens / 1_000_000 * rate_out)

            marker = "[>128K] " if in_tokens > TOKEN_THRESHOLD else ""
            debug_string = f" ⚙️ DEBUG [{action}] | {marker}IN: {in_tokens} | OUT: {out_tokens} | Cost: ${call_cost:.6f}"

            if custom_log_widget:
                self.log(custom_log_widget, debug_string)

            return debug_string, call_cost
        except Exception as e:
            print(f"Cost tracker error: {e}")
            return "", 0.0

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TNotebook.Tab", font=("Segoe UI", 12, "bold"), padding=[15, 8])
        style.configure("Treeview", font=("Segoe UI", 12), rowheight=30)
        style.configure("Treeview.Heading", font=("Segoe UI", 12, "bold"))

        header = tk.Frame(self.root, bg=self.primary_color, pady=15, bd=1, relief="solid")
        header.pack(fill="x")
        tk.Label(header, text="AI CONSULTATION SYSTEM", font=("Segoe UI", 16, "bold"), fg="white",
                 bg=self.primary_color).pack()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=10)

        self.tab_config = tk.Frame(self.notebook, bg=self.bg_color)
        self.tab_navigator = tk.Frame(self.notebook, bg=self.bg_color)
        self.tab_preparation = tk.Frame(self.notebook, bg=self.bg_color)

        self.notebook.add(self.tab_config, text=" Settings ")
        self.notebook.add(self.tab_navigator, text=" Query ")
        self.notebook.add(self.tab_preparation, text=" Data Processing ")

        self.setup_tab_config()
        self.setup_tab_navigator()
        self.setup_tab_preparation()

        self.update_synthesis_layout()

        self.notebook.bind("<<NotebookTabChanged>>", self.check_synchronization)
        self.root.after(1000, self.check_synchronization)
        self.root.after(1500, self.sync_credentials_from_db)

    def _scroll_canvas_config(self, event):
        if isinstance(event.widget, ttk.Treeview):
            return
        self.canvas_config.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_mousewheel_config(self, event):
        self.canvas_config.bind_all("<MouseWheel>", self._scroll_canvas_config)

    def _unbind_mousewheel_config(self, event):
        self.canvas_config.unbind_all("<MouseWheel>")

    def _bind_mousewheel_sources(self, event):
        self.canvas_sources.bind_all("<MouseWheel>", lambda e: self.canvas_sources.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    def _unbind_mousewheel_sources(self, event):
        self.canvas_sources.unbind_all("<MouseWheel>")

    def setup_tab_config(self):
        f_scroll_container = tk.Frame(self.tab_config, bg=self.bg_color)
        f_scroll_container.pack(side="top", fill="both", expand=True)

        self.canvas_config = tk.Canvas(f_scroll_container, bg=self.bg_color, highlightthickness=0)
        self.scroll_config = ttk.Scrollbar(f_scroll_container, orient="vertical", command=self.canvas_config.yview)
        self.canvas_config.configure(yscrollcommand=self.scroll_config.set)

        self.f_config_inner = tk.Frame(self.canvas_config, bg=self.bg_color)
        self.f_config_inner.bind("<Configure>", lambda e: self.canvas_config.configure(scrollregion=self.canvas_config.bbox("all")))

        self.canvas_window_config = self.canvas_config.create_window((0, 0), window=self.f_config_inner, anchor="nw")
        self.canvas_config.bind("<Configure>", lambda e: self.canvas_config.itemconfig(self.canvas_window_config, width=e.width))

        self.canvas_config.pack(side="left", fill="both", expand=True)
        self.scroll_config.pack(side="right", fill="y")

        self.canvas_config.bind("<Enter>", self._bind_mousewheel_config)
        self.canvas_config.bind("<Leave>", self._unbind_mousewheel_config)

        card = tk.Frame(self.f_config_inner, bg=self.card_color, bd=1, relief="solid")
        card.pack(pady=20, padx=50, fill="x")

        tk.Label(card, text="Data Sources", font=("Segoe UI", 14, "bold"), bg=self.card_color).pack(pady=(15, 5))

        f1 = tk.Frame(card, bg=self.card_color)
        f1.pack(fill="x", padx=30, pady=10)
        tk.Button(f1, text="Select Database", width=28, bg=self.btn_bg_light, fg="black", bd=1, relief="solid",
                  font=("Segoe UI", 12, "bold"), command=self.select_db_file).pack(side="left")
        tk.Entry(f1, textvariable=self.db_path, font=("Segoe UI", 12), state="readonly", bd=1, relief="solid",
                 bg="#F9FAFB").pack(side="left", fill="x", expand=True, padx=10, ipady=4)

        f2 = tk.Frame(card, bg=self.card_color)
        f2.pack(fill="x", padx=30, pady=10)
        tk.Button(f2, text="Select Root Directory", width=28, bg=self.btn_bg_light, fg="black", bd=1,
                  relief="solid",
                  font=("Segoe UI", 12, "bold"), command=self.select_folder).pack(side="left")
        tk.Entry(f2, textvariable=self.folder_path, font=("Segoe UI", 12), state="readonly", bd=1, relief="solid",
                 bg="#F9FAFB").pack(side="left", fill="x", expand=True, padx=10, ipady=4)

        f_tree_ext = tk.Frame(card, bg=self.card_color)
        f_tree_ext.pack(fill="x", padx=30, pady=(5, 15))

        tk.Label(f_tree_ext, text="Select specific folders to analyze (Unchecked ones will be ignored):",
                 font=("Segoe UI", 11), fg="#4B5563", bg=self.card_color).pack(anchor="w")

        self.tree_extraction = CheckboxFolderTree(f_tree_ext, self.folder_path.get(), SELECTED_EXTRACTION_FOLDERS, height=6)
        self.tree_extraction.pack(fill="x", expand=True, pady=5)
        self.tree_extraction.bind("<<TreeChecked>>", lambda e: update_config("extraction_folders", SELECTED_EXTRACTION_FOLDERS))

        card_api = tk.Frame(self.f_config_inner, bg=self.card_color, bd=1, relief="solid")
        card_api.pack(pady=10, padx=50, fill="x")

        tk.Label(card_api, text="AI Parameters (Google Gemini)", font=("Segoe UI", 14, "bold"), bg=self.card_color).pack(pady=(15, 5))

        f_options = tk.Frame(card_api, bg=self.card_color)
        f_options.pack(fill="x", padx=30, pady=(5, 10))
        tk.Checkbutton(f_options, text="Enable Generative Synthesis (RAG) to formulate discursive responses",
                       variable=self.use_synthesis_var, command=self.save_synthesis_state,
                       font=("Segoe UI", 12), bg=self.card_color, activebackground=self.card_color).pack(side="left")

        def toggle_rates_button():
            if self.use_debug_var.get():
                pwd = simpledialog.askstring("Authentication", "Enter system password to enable Debug:",
                                             parent=self.root, show='*')

                if pwd == ACCESS_PASSWORD:
                    self.btn_rates.pack(side="left", padx=15)
                else:
                    self.use_debug_var.set(False)
                    self.btn_rates.pack_forget()

                    if pwd is not None:
                        messagebox.showerror("Access Denied", "Incorrect password. Debug mode not enabled.",
                                             parent=self.root)
            else:
                self.btn_rates.pack_forget()

        tk.Checkbutton(f_options, text="Debug Mode (Show cost and token estimates on screen)",
                       variable=self.use_debug_var, command=toggle_rates_button, font=("Segoe UI", 12),
                       bg=self.card_color, activebackground=self.card_color, fg="#D97706").pack(side="left", padx=(20, 0))

        self.btn_rates = tk.Button(f_options, text="Rates", font=("Segoe UI", 10, "bold"),
                                   bg="#FEF08A", bd=1, relief="solid", command=self.open_rates_popup)

        toggle_rates_button()

        f_api = tk.Frame(card_api, bg=self.card_color)
        f_api.pack(fill="x", padx=30, pady=(0, 10))
        tk.Label(f_api, text="Access Key:", font=("Segoe UI", 12), bg=self.card_color).pack(side="left")

        self.ent_apikey = tk.Entry(f_api, font=("Segoe UI", 13), bd=1, relief="solid", bg="#FFFFFF")
        self.ent_apikey.pack(side="left", fill="x", expand=True, padx=10, ipady=4)
        self.ent_apikey.insert(0, "***************************************")
        self.ent_apikey.config(state="readonly")

        self.btn_lock = tk.Button(f_api, text="🔒 Edit", font=("Segoe UI", 11, "bold"), fg="black",
                                  bg=self.btn_bg_light, bd=1, relief="solid", width=14, command=self.toggle_lock)
        self.btn_lock.pack(side="left", padx=5)

        self.btn_save_api = tk.Button(f_api, text="Save", bg=self.btn_bg_green, fg="black",
                                      font=("Segoe UI", 11, "bold"), bd=1, relief="solid", width=12, state="disabled",
                                      disabledforeground="#4B5563", command=self.save_api_key)
        self.btn_save_api.pack(side="left", padx=5)

        self.btn_change_pwd = tk.Button(f_api, text="Change Password", bg=self.btn_bg_light, fg="black",
                                        font=("Segoe UI", 11, "bold"), bd=1, relief="solid", width=16,
                                        command=self.change_system_password)
        self.btn_change_pwd.pack(side="left", padx=5)

        f_models = tk.Frame(card_api, bg="#F9FAFB", bd=1, relief="solid")
        f_models.pack(fill="x", padx=30, pady=(10, 15))
        tk.Label(f_models, text="API Connection Status:", font=("Segoe UI", 11, "bold"), bg="#F9FAFB").pack(side="left", padx=10, pady=5)

        self.lbl_models = tk.Label(f_models, text="Waiting", font=("Segoe UI", 11), bg="#F9FAFB")
        self.lbl_models.pack(side="left", padx=5, pady=5)

        bg_color_btn = "#E5E7EB"
        fg_color_btn = "#1F2937"
        hover_color_btn = "#D1D5DB"

        self.btn_change_models = tk.Button(f_models, text="+", font=("Segoe UI", 14, "bold"),
                                           bg=bg_color_btn, fg=fg_color_btn, activebackground=hover_color_btn,
                                           bd=1, relief="solid", cursor="hand2", width=3, height=1,
                                           command=self.open_models_popup)

        def on_enter(e):
            self.btn_change_models['background'] = hover_color_btn

        def on_leave(e):
            self.btn_change_models['background'] = bg_color_btn

        self.btn_change_models.bind("<Enter>", on_enter)
        self.btn_change_models.bind("<Leave>", on_leave)

        self.btn_change_models.pack(side="left", padx=10, pady=5)

        self.f_sync_warning = tk.Frame(self.tab_config, bg="#FEF2F2", bd=1, relief="solid",
                                       highlightbackground="#F87171", highlightthickness=1)
        self.lbl_sync_warning = tk.Label(self.f_sync_warning,
                                         text="⚠️ WARNING: The Database is not synchronized with the folder files.\nGo to the 'Data Processing' tab and run extraction and vectorization, one of the phases might be missing.",
                                         font=("Segoe UI", 12, "bold"), fg="#B91C1C", bg="#FEF2F2", justify="center")
        self.lbl_sync_warning.pack(pady=10, padx=10)

    def save_synthesis_state(self):
        update_config("use_synthesis", self.use_synthesis_var.get())
        self.update_synthesis_layout()

    def update_synthesis_layout(self):
        if not hasattr(self, 'f_response'):
            return

        if self.use_synthesis_var.get():
            self.f_response.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        else:
            self.f_response.grid_remove()

    def update_models_from_api(self):
        if not self.client:
            return
        self.root.after(0, lambda: self.lbl_models.config(text="Verification in progress..."))
        try:
            embed_found, text_found = [], []
            for m in self.client.models.list():
                name = m.name.replace("models/", "")
                name_lower = name.lower()

                if "embed" in name_lower:
                    embed_found.append(name)
                elif "gemini" in name_lower and "nano" not in name_lower:
                    if "flash" in name_lower or "pro" in name_lower:
                        text_found.append(name)

            flash_models = sorted([m for m in text_found if "flash" in m], reverse=True)
            pro_models = sorted([m for m in text_found if "pro" in m], reverse=True)
            embed_found = sorted(embed_found, reverse=True)

            self.available_flash_lists = flash_models if flash_models else text_found
            self.available_pro_lists = pro_models if pro_models else text_found
            self.available_embed_lists = embed_found

            if flash_models:
                self.extraction_model = flash_models[0]
            else:
                self.extraction_model = text_found[0] if text_found else 'gemini-1.5-flash'

            if pro_models:
                self.response_model = pro_models[0]
            else:
                self.response_model = flash_models[0] if flash_models else 'gemini-1.5-pro'

            if embed_found:
                self.embed_model = embed_found[0]
            else:
                self.embed_model = 'text-embedding-004'

            msg_ui = f"Extraction: {self.extraction_model} | Analysis: {self.response_model} | Embedding: {self.embed_model}"
            self.root.after(0, lambda: self.lbl_models.config(text=msg_ui, fg="#059669"))
        except Exception:
            self.root.after(0, lambda: self.lbl_models.config(text="API connection error.", fg="#DC2626"))

    def toggle_lock(self):
        if self.btn_lock.cget("text") == "🔒 Edit":
            pwd = simpledialog.askstring("Authentication", "Enter password:", parent=self.root, show='*')
            if pwd == ACCESS_PASSWORD:
                self.ent_apikey.config(state="normal")
                self.ent_apikey.delete(0, tk.END)
                if API_KEY:
                    self.ent_apikey.insert(0, API_KEY)

                self.btn_lock.config(text="🔓 Lock", bg="#FEF08A")
                self.btn_save_api.config(state="normal")
            elif pwd is not None:
                messagebox.showerror("Error", "Authentication failed.", parent=self.root)
        else:
            self.ent_apikey.delete(0, tk.END)
            self.ent_apikey.insert(0, "***************************************")
            self.ent_apikey.config(state="readonly")
            self.btn_lock.config(text="🔒 Edit", bg=self.btn_bg_light)
            self.btn_save_api.config(state="disabled")

    def save_api_key(self):
        new_key = self.ent_apikey.get().strip()
        if not new_key or new_key.startswith("***"):
            return
        try:
            encrypted_key = encrypt_key(new_key, ACCESS_PASSWORD)
            update_config("geminiapi", encrypted_key)

            self.client = genai.Client(api_key=new_key)
            global API_KEY
            API_KEY = new_key

            self.ent_apikey.delete(0, tk.END)
            self.ent_apikey.insert(0, "***************************************")
            self.ent_apikey.config(state="readonly")
            self.btn_lock.config(text="🔒 Edit", bg=self.btn_bg_light)
            self.btn_save_api.config(state="disabled")

            threading.Thread(target=self.update_models_from_api, daemon=True).start()

            self.save_credentials_to_db()
            messagebox.showinfo("Operation Completed", "Credentials updated and distributed to all terminals.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Error", f"Unable to validate the key:\n{e}", parent=self.root)

    def change_system_password(self):
        global ACCESS_PASSWORD
        old_pwd = simpledialog.askstring("Security", "Enter current password:", parent=self.root, show='*')
        if old_pwd != ACCESS_PASSWORD:
            if old_pwd is not None:
                messagebox.showerror("Error", "Incorrect password.", parent=self.root)
            return

        new_pwd = simpledialog.askstring("Security", "Enter NEW password:", parent=self.root, show='*')
        if not new_pwd:
            return

        try:
            if API_KEY:
                new_encrypted_key = encrypt_key(API_KEY, new_pwd)
                update_config("geminiapi", new_encrypted_key)

            ACCESS_PASSWORD = new_pwd
            pwd_b64 = base64.b64encode(new_pwd.encode('utf-8')).decode('utf-8')
            update_config("app_pwd", pwd_b64)

            self.save_credentials_to_db()
            messagebox.showinfo("Operation Completed", "System password updated and distributed over the network.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Error", f"Unable to update the password:\n{e}", parent=self.root)

    def sync_credentials_from_db(self):
        global API_KEY, ACCESS_PASSWORD
        db = self.db_path.get()
        if db == "No file selected" or not os.path.exists(db):
            return

        try:
            conn = get_db_connection(db)
            conn.execute("CREATE TABLE IF NOT EXISTS admin_config (key TEXT PRIMARY KEY, value TEXT)")
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM admin_config")
            rows = cursor.fetchall()
            conn.close()

            data = {row[0]: row[1] for row in rows}

            if "encrypted_api" in data:
                dec_api = decrypt_key(data["encrypted_api"], SYSTEM_SECRET_KEY)
                if dec_api and dec_api != API_KEY:
                    API_KEY = dec_api
                    self.client = genai.Client(api_key=API_KEY)
                    threading.Thread(target=self.update_models_from_api, daemon=True).start()

            if "encrypted_pwd" in data:
                dec_pwd = decrypt_key(data["encrypted_pwd"], SYSTEM_SECRET_KEY)
                if dec_pwd:
                    ACCESS_PASSWORD = dec_pwd

        except Exception as e:
            print(f"Error reading configuration from DB: {e}")

    def save_credentials_to_db(self):
        global API_KEY, ACCESS_PASSWORD
        db = self.db_path.get()
        if db == "No file selected" or not os.path.exists(db):
            messagebox.showwarning("Warning", "Select a Database before saving network credentials.", parent=self.root)
            return

        try:
            conn = get_db_connection(db)
            conn.execute("CREATE TABLE IF NOT EXISTS admin_config (key TEXT PRIMARY KEY, value TEXT)")

            encrypted_api = encrypt_key(API_KEY, SYSTEM_SECRET_KEY)
            encrypted_pwd = encrypt_key(ACCESS_PASSWORD, SYSTEM_SECRET_KEY)

            conn.execute("REPLACE INTO admin_config (key, value) VALUES ('encrypted_api', ?)", (encrypted_api,))
            conn.execute("REPLACE INTO admin_config (key, value) VALUES ('encrypted_pwd', ?)", (encrypted_pwd,))

            conn.commit()
            conn.close()
        except Exception as e:
            messagebox.showerror("DB Error", f"Unable to save credentials in the Database:\n{e}", parent=self.root)

    def select_db_file(self):
        path = filedialog.askopenfilename(parent=self.root, defaultextension=".db", filetypes=[("SQLite Database", "*.db")])
        if not path:
            if messagebox.askyesno("New Database", "Proceed with creating a new database?", parent=self.root):
                path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".db", filetypes=[("SQLite Database", "*.db")])
        if path:
            self.db_path.set(path)
            update_config("db_path", path)
            self.check_synchronization()
            self.sync_credentials_from_db()

    def select_folder(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.folder_path.set(path)
            update_config("folder_path", path)

            self.search_filter_state.clear()

            global SELECTED_EXTRACTION_FOLDERS
            SELECTED_EXTRACTION_FOLDERS.clear()
            update_config("extraction_folders", SELECTED_EXTRACTION_FOLDERS)

            self.tree_extraction.root_dir = path
            self.tree_extraction.populate_root()
            self.check_synchronization()

    def setup_tab_navigator(self):
        f_in = tk.Frame(self.tab_navigator, bg=self.bg_color)
        f_in.pack(fill="x", padx=20, pady=10)

        tk.Label(f_in, text="Question:", font=("Segoe UI", 13, "bold"), bg=self.bg_color).pack(anchor="w", pady=(0, 2))

        self.ent_question = tk.Entry(f_in, font=("Segoe UI", 14), bd=1, relief="solid")
        self.ent_question.pack(side="left", fill="x", expand=True, ipady=6)

        self.btn_filters = tk.Button(f_in, text="Select Folders", bg="#FCD34D", fg="black",
                                     font=("Segoe UI", 12, "bold"), bd=1, relief="solid", command=self.open_filters_popup)
        self.btn_filters.pack(side="left", padx=10, ipady=5)

        self.btn_nav_search = tk.Button(f_in, text="PROCESS", bg=self.btn_bg_blue, fg="black",
                                        font=("Segoe UI", 12, "bold"), bd=1, relief="solid", width=14,
                                        disabledforeground="#4B5563", command=self.run_search)
        self.btn_nav_search.pack(side="left", padx=5, ipady=5)
        self.ent_question.bind("<Return>", lambda e: self.run_search())

        f_content = tk.Frame(self.tab_navigator, bg=self.bg_color)
        f_content.pack(fill="both", expand=True, padx=20, pady=10)

        f_content.grid_columnconfigure(0, weight=1)
        f_content.grid_rowconfigure(0, weight=3)
        f_content.grid_rowconfigure(1, weight=1)

        self.f_response = tk.Frame(f_content, bg=self.bg_color)
        self.f_response.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        tk.Label(self.f_response, text="Response:", font=("Segoe UI", 13, "bold"), bg=self.bg_color).pack(anchor="w")
        self.txt_nav_response = scrolledtext.ScrolledText(self.f_response, wrap=tk.WORD, font=("Segoe UI", 13), bd=1,
                                                          relief="solid", padx=10, pady=10)
        self.txt_nav_response.pack(fill="both", expand=True)

        f_sources_outer = tk.Frame(f_content, bg=self.bg_color)
        f_sources_outer.grid(row=1, column=0, sticky="nsew")

        tk.Label(f_sources_outer, text="Source Files:", font=("Segoe UI", 13, "bold"), bg=self.bg_color).pack(anchor="w")

        self.canvas_sources = tk.Canvas(f_sources_outer, bg=self.bg_color, highlightthickness=0, bd=1, relief="solid")
        self.scroll_sources = ttk.Scrollbar(f_sources_outer, orient="vertical", command=self.canvas_sources.yview)
        self.canvas_sources.configure(yscrollcommand=self.scroll_sources.set)

        self.f_nav_sources = tk.Frame(self.canvas_sources, bg=self.bg_color)
        self.f_nav_sources.bind("<Configure>", lambda e: self.canvas_sources.configure(scrollregion=self.canvas_sources.bbox("all")))
        self.canvas_window = self.canvas_sources.create_window((0, 0), window=self.f_nav_sources, anchor="nw")

        self.canvas_sources.bind("<Configure>", lambda e: self.canvas_sources.itemconfig(self.canvas_window, width=e.width - 4))
        self.canvas_sources.bind("<Enter>", self._bind_mousewheel_sources)
        self.canvas_sources.bind("<Leave>", self._unbind_mousewheel_sources)

        self.canvas_sources.pack(side="left", fill="both", expand=True)
        self.scroll_sources.pack(side="right", fill="y")

    def open_filters_popup(self):
        db = self.db_path.get()
        if not db or db == "No file selected" or not os.path.exists(db):
            messagebox.showwarning("Warning", "Select a valid Database in Settings before filtering.", parent=self.root)
            return

        top = tk.Toplevel(self.root)
        top.title("Search Filters")

        popup_width = 500
        popup_height = 550
        screen_width = top.winfo_screenwidth()
        screen_height = top.winfo_screenheight()
        center_x = int(screen_width / 2 - popup_width / 2)
        center_y = int(screen_height / 2 - popup_height / 2)
        top.geometry(f"{popup_width}x{popup_height}+{center_x}+{center_y}")

        top.transient(self.root)
        top.grab_set()

        tk.Label(top, text="Select folders to INCLUDE in the response:", font=("Segoe UI", 12, "bold"), pady=10).pack()

        container = tk.Frame(top, bd=1, relief="solid", bg="#FFFFFF")
        container.pack(fill="both", expand=True, padx=20, pady=5)

        canvas = tk.Canvas(container, bg="#FFFFFF", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#FFFFFF")

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _bind_popup_mousewheel(event):
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        def _unbind_popup_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_popup_mousewheel)
        canvas.bind("<Leave>", _unbind_popup_mousewheel)

        db_folders = set()
        try:
            conn = get_db_connection(db)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='manual_extracts'")
            if cursor.fetchone():
                cursor.execute("SELECT DISTINCT original_file FROM manual_extracts")
                for row in cursor.fetchall():
                    rel_path = row[0]
                    parts = Path(rel_path).parts
                    top_level = parts[0] if len(parts) > 1 else "."
                    db_folders.add(top_level)
            conn.close()
        except Exception as e:
            print(f"Error reading DB folders: {e}")

        if not db_folders:
            tk.Label(scrollable_frame, text="No data in Database.\nRun Data Extraction first.", font=("Segoe UI", 11), bg="#FFFFFF", fg="#6B7280").pack(pady=20)
            items = []
        else:
            items = sorted(list(db_folders), key=lambda x: (x != ".", x.lower()))

        self.temp_filter_vars = {}
        for item in items:
            if item not in self.search_filter_state:
                self.search_filter_state[item] = True

            var = tk.BooleanVar(value=self.search_filter_state[item])
            self.temp_filter_vars[item] = var

            cb_text = "📄 [Files scattered in Main Folder]" if item == "." else f"📁 {item}"
            cb = tk.Checkbutton(scrollable_frame, text=cb_text, variable=var, font=("Segoe UI", 12), bg="#FFFFFF")
            cb.pack(anchor="w", padx=10, pady=4)

        def save_and_close():
            for item, var in self.temp_filter_vars.items():
                self.search_filter_state[item] = var.get()
            top.destroy()

        tk.Button(top, text="Apply Filters", bg=self.btn_bg_green, font=("Segoe UI", 12, "bold"), command=save_and_close).pack(pady=15, ipady=6, ipadx=15)

    def open_models_popup(self):
        if not hasattr(self, 'available_flash_lists'):
            tk.messagebox.showwarning("Wait", "Please wait for initial models loading before modifying them.")
            return

        popup = tk.Toplevel(self.root)
        popup.title("AI Models Settings")

        popup_width = 450
        popup_height = 320

        screen_width = popup.winfo_screenwidth()
        screen_height = popup.winfo_screenheight()

        x_coordinate = int((screen_width / 2) - (popup_width / 2))
        y_coordinate = int((screen_height / 2) - (popup_height / 2))

        popup.geometry(f"{popup_width}x{popup_height}+{x_coordinate}+{y_coordinate}")

        popup.transient(self.root)
        popup.grab_set()
        popup.configure(bg=self.bg_color)

        popup.resizable(False, False)

        title_font = ("Segoe UI", 10, "bold")

        tk.Label(popup, text="Extraction Model (Fast):", font=title_font, bg=self.bg_color).pack(pady=(15, 2))
        cmb_extraction = ttk.Combobox(popup, values=self.available_flash_lists, state="readonly", width=50)
        cmb_extraction.set(self.extraction_model)
        cmb_extraction.pack()

        tk.Label(popup, text="Analysis Model (Reasoning):", font=title_font, bg=self.bg_color).pack(pady=(15, 2))
        cmb_analysis = ttk.Combobox(popup, values=self.available_pro_lists, state="readonly", width=50)
        cmb_analysis.set(self.response_model)
        cmb_analysis.pack()

        tk.Label(popup, text="Vectorization Model (Embedding):", font=title_font, bg=self.bg_color).pack(pady=(15, 2))
        cmb_embed = ttk.Combobox(popup, values=self.available_embed_lists, state="readonly", width=50)
        cmb_embed.set(self.embed_model)
        cmb_embed.pack()

        def save_and_close():
            self.extraction_model = cmb_extraction.get()
            self.response_model = cmb_analysis.get()
            self.embed_model = cmb_embed.get()

            msg_ui = f"Extraction: {self.extraction_model} | Analysis: {self.response_model} | Embedding: {self.embed_model}"
            self.lbl_models.config(text=msg_ui, fg="#059669")
            popup.destroy()

        tk.Button(popup, text="Apply and Close", bg=self.btn_bg_blue, font=("Segoe UI", 11, "bold"),
                  command=save_and_close, bd=1, relief="solid").pack(pady=25)

    def setup_tab_preparation(self):
        f_stop = tk.Frame(self.tab_preparation, bg=self.bg_color)
        f_stop.pack(fill="x", padx=20, pady=(10, 0))
        self.btn_stop_global = tk.Button(f_stop, text="INTERRUPT CURRENT PROCESS", bg=self.btn_bg_red, fg="black",
                                         bd=1, relief="solid",
                                         font=("Segoe UI", 12, "bold"), state="disabled", disabledforeground="#4B5563",
                                         command=self.request_interrupt)
        self.btn_stop_global.pack(fill="x", ipady=5)

        f_ext = tk.Frame(self.tab_preparation, bg=self.card_color, bd=1, relief="solid")
        f_ext.pack(fill="both", expand=True, padx=20, pady=(15, 5))

        tk.Label(f_ext, text="1. Text Extraction (OCR/Parse)", font=("Segoe UI", 13, "bold"), bg=self.card_color).pack(anchor="w", padx=10, pady=(10, 0))

        b_ext = tk.Frame(f_ext, bg=self.card_color)
        b_ext.pack(fill="x", pady=5, padx=10)
        self.btn_ext_start = tk.Button(b_ext, text="START EXTRACTION", bg=self.btn_bg_blue, fg="black", bd=1,
                                       relief="solid", font=("Segoe UI", 11, "bold"), disabledforeground="#4B5563",
                                       command=self.run_extraction)
        self.btn_ext_start.pack(fill="x", ipady=5)

        f_prog_ext = tk.Frame(f_ext, bg=self.card_color)
        f_prog_ext.pack(fill="x", padx=10, pady=5)
        self.prog_ext = ttk.Progressbar(f_prog_ext, orient="horizontal", mode="determinate")
        self.prog_ext.pack(side="left", fill="x", expand=True)
        self.lbl_eta_ext = tk.Label(f_prog_ext, text="Waiting...", bg=self.card_color, font=("Segoe UI", 11))
        self.lbl_eta_ext.pack(side="right", padx=(10, 0))

        self.log_ext = scrolledtext.ScrolledText(f_ext, bg="#111827", fg="#D1D5DB", font=("Consolas", 11), bd=1,
                                                 relief="solid", height=6)
        self.log_ext.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        f_vet = tk.Frame(self.tab_preparation, bg=self.card_color, bd=1, relief="solid")
        f_vet.pack(fill="both", expand=True, padx=20, pady=(5, 15))

        tk.Label(f_vet, text="2. Vector Processing (Embedding)", font=("Segoe UI", 13, "bold"), bg=self.card_color).pack(anchor="w", padx=10, pady=(10, 0))

        b_vet = tk.Frame(f_vet, bg=self.card_color)
        b_vet.pack(fill="x", pady=5, padx=10)
        self.btn_vet_start = tk.Button(b_vet, text="START VECTORIZATION", bg=self.btn_bg_purple, fg="black", bd=1,
                                       relief="solid", font=("Segoe UI", 11, "bold"), disabledforeground="#4B5563",
                                       command=self.run_vectorization)
        self.btn_vet_start.pack(fill="x", ipady=5)

        f_prog_vet = tk.Frame(f_vet, bg=self.card_color)
        f_prog_vet.pack(fill="x", padx=10, pady=5)
        self.prog_vet = ttk.Progressbar(f_prog_vet, orient="horizontal", mode="determinate")
        self.prog_vet.pack(side="left", fill="x", expand=True)
        self.lbl_eta_vet = tk.Label(f_prog_vet, text="Waiting...", bg=self.card_color, font=("Segoe UI", 11))
        self.lbl_eta_vet.pack(side="right", padx=(10, 0))

        self.log_vet = scrolledtext.ScrolledText(f_vet, bg="#111827", fg="#D1D5DB", font=("Consolas", 11), bd=1,
                                                 relief="solid", height=6)
        self.log_vet.pack(fill="both", expand=True, padx=10, pady=(5, 10))

    def format_eta(self, seconds):
        if seconds < 0 or seconds > 86400:
            return "Calculation in progress..."
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        sec = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def update_prog_ext(self, completed, total, eta_str):
        self.prog_ext['value'] = completed
        self.lbl_eta_ext.config(text=f"Processed: {completed}/{total} | ETA: {eta_str}")

    def update_prog_vet(self, completed, total, eta_str):
        self.prog_vet['value'] = completed
        self.lbl_eta_vet.config(text=f"Processed: {completed}/{total} | ETA: {eta_str}")

    # ==========================================
    # OPERATIONAL LOGIC
    # ==========================================
    def check_synchronization(self, event=None):
        threading.Thread(target=self._thread_check_sync, daemon=True).start()

    def _thread_check_sync(self):
        db = self.db_path.get()
        folder = self.folder_path.get()

        self.root.after(0, self.f_sync_warning.pack_forget)

        if db == "No file selected" or folder == "No directory selected": return
        if not os.path.exists(db) or not os.path.exists(folder): return

        discordant = False
        try:
            conn = get_db_connection(db)
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_tracker'")
            if not cursor.fetchone():
                discordant = True
            else:
                cursor.execute("SELECT original_file, modified_timestamp, status FROM file_tracker")
                db_files = {row[0]: {'mtime': row[1], 'status': row[2]} for row in cursor.fetchall()}

                pdf_files = []
                for root_dir in SELECTED_EXTRACTION_FOLDERS:
                    if os.path.exists(root_dir):
                        for file in os.listdir(root_dir):
                            if file.lower().endswith(".pdf"):
                                abs_path = os.path.join(root_dir, file)
                                rel_path = os.path.relpath(abs_path, folder)
                                pdf_files.append(rel_path)

                if len(pdf_files) != len(db_files):
                    discordant = True
                else:
                    for rel_path in pdf_files:
                        if rel_path not in db_files:
                            discordant = True
                            break

                        mtime = os.path.getmtime(os.path.join(folder, rel_path))
                        if db_files[rel_path]['mtime'] != mtime or db_files[rel_path]['status'] != 'COMPLETED':
                            discordant = True
                            break

                if not discordant:
                    cursor.execute("SELECT 1 FROM manual_extracts WHERE embedding_json IS NULL LIMIT 1")
                    if cursor.fetchone(): discordant = True

            conn.close()
        except Exception:
            discordant = True

        if discordant:
            self.root.after(0, lambda: self.f_sync_warning.pack(side="bottom", fill="x", padx=50, pady=(0, 20)))

    def log(self, widget, msg):
        widget.config(state="normal")
        widget.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        widget.see(tk.END)
        widget.config(state="disabled")
        self.root.update_idletasks()

    def check_prerequisites(self, check_folder=False):
        if not self.client:
            messagebox.showwarning("Warning", "Configure API before proceeding.", parent=self.root)
            self.notebook.select(self.tab_config)
            return False
        if self.db_path.get() == "No file selected":
            messagebox.showwarning("Warning", "Select a DB.", parent=self.root)
            return False
        if check_folder and self.folder_path.get() == "No directory selected":
            messagebox.showwarning("Warning", "Select folder.", parent=self.root)
            return False
        return True

    # --- NAVIGATOR LOGIC ---
    def run_search(self):
        if not self.check_prerequisites(check_folder=True): return
        question = self.ent_question.get().strip()
        if not question: return

        self.btn_nav_search.config(state="disabled", text="IN PROGRESS...", bg="#9CA3AF")
        self.ent_question.config(state="disabled")

        self.txt_nav_response.config(state="normal")
        self.txt_nav_response.delete(1.0, tk.END)
        self.txt_nav_response.insert(tk.END, " Processing..." if self.use_synthesis_var.get() else " Semantic search in progress...")
        self.txt_nav_response.config(state="disabled")

        threading.Thread(target=self.search_thread, args=(question,), daemon=True).start()

    def search_thread(self, question):
        try:
            emb_q = self.client.models.embed_content(model=self.embed_model, contents=question).embeddings[0].values

            conn = get_db_connection(self.db_path.get())
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='manual_extracts'")
            if not cursor.fetchone():
                conn.close()
                self.root.after(0, lambda: self.update_nav_ui("Error: Unstructured data.", []))
                return

            cursor.execute("SELECT macro_title, section, content, embedding_json, original_file FROM manual_extracts WHERE embedding_json IS NOT NULL")
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                self.root.after(0, lambda: self.update_nav_ui("No indexed data present.", []))
                return

            # --- HYBRID SEARCH PREPARATION AND AI SYNONYMS ---
            question_lower = question.lower()
            base_words = [p.strip(".,;:?!()'\"") for p in question_lower.split() if len(p) > 2]

            alphanumeric_codes = [p for p in base_words if any(c.isdigit() for c in p) and any(c.isalpha() for c in p)]
            words_to_expand = [p for p in base_words if p not in alphanumeric_codes]

            expanded_words = set(base_words)

            if words_to_expand:
                try:
                    synonyms_prompt = f"""Act as a rigorous technical terminologist.
                    Your task is to find EXTREMELY precise synonyms or exact jargon variants for these keywords: {', '.join(words_to_expand)}

                    ABSOLUTE AND BINDING RULES:
                    1. ONLY return a comma-separated list of words. No additional text.
                    2. Synonyms must be 100% INTERCHANGEABLE in the technical/industrial context (e.g., "assembly" = "mounting").
                    3. BANNED: generic associations or similar components (e.g., if the word is "screw", DO NOT answer "bolt" or "fastener").
                    4. If a word DOES NOT have a direct, strict, and unambiguous synonym, IGNORE IT completely. Do not force an answer.
                    5. Use the singular form."""

                    flash_model = getattr(self, 'extraction_model', 'gemini-1.5-flash')

                    synonyms_resp = self.client.models.generate_content(
                        model=flash_model,
                        contents=synonyms_prompt,
                        config=types.GenerateContentConfig(temperature=0.1)
                    )

                    found_synonyms = [s.strip("., \n").lower() for s in synonyms_resp.text.split(",")]
                    expanded_words.update(found_synonyms)
                except Exception as e:
                    print(f"Fallback: Synonym expansion failed ({e})")

            keywords = list(expanded_words)
            # --------------------------------------------------

            scores = []
            for r in rows:
                db_rel_path = r[4]

                path_parts = Path(db_rel_path).parts
                top_level = path_parts[0] if len(path_parts) > 1 else "."

                included = self.search_filter_state.get(top_level, True)
                if not included:
                    continue

                # 1. Pure Semantic Score
                semantic_score = cosine_similarity(emb_q, json.loads(r[3]))
                final_score = semantic_score

                # 2. HYBRID OVERRIDE
                file_name_lower = os.path.basename(db_rel_path).lower()
                folder_name_lower = os.path.dirname(db_rel_path).lower()
                full_text_lower = f"{r[0]} {r[1]} {r[2]}".lower()

                for word in keywords:
                    if word in file_name_lower:
                        final_score += 0.05
                    elif word in folder_name_lower:
                        final_score += 0.05

                for code in alphanumeric_codes:
                    if code in file_name_lower:
                        final_score = max(final_score, 0.95)
                    elif code in full_text_lower:
                        final_score = max(final_score, 0.75)

                scores.append((final_score, r[0], r[1], r[2], db_rel_path))

            scores.sort(key=lambda x: x[0], reverse=True)
            top_valid = [x for x in scores if x[0] >= RELEVANCE_THRESHOLD][:NUMBER_RELATED_DOCS]

            if not top_valid:
                self.root.after(0, lambda: self.update_nav_ui("No adequate match found in included documents.", []))
                return

            file_scores = {}
            for x in top_valid:
                current_score, _, _, _, rel_path = x
                if rel_path:
                    if rel_path not in file_scores or current_score > file_scores[rel_path]:
                        file_scores[rel_path] = min(current_score, 0.99)

            sorted_files = sorted(file_scores.items(), key=lambda item: item[1], reverse=True)

            if self.use_synthesis_var.get():
                context_list = []
                for i, x in enumerate(top_valid):
                    _, macro_title, section, content, rel_path = x
                    file_name = os.path.basename(rel_path)
                    folders = os.path.dirname(rel_path)
                    if not folders:
                        folders = "Main Folder (Generic)"

                    block = f"FILE [Rank: {i + 1}]: {file_name}\nCATEGORY/FOLDERS: {folders}\nSECTION: {section}\nCONTENT:\n{content}\n---"
                    context_list.append(block)

                context = "\n".join(context_list)

                analyst_prompt = f"""Act as a Technical Analyst.
            ABSOLUTE FORMATTING AND SORTING RULES:
            - START IMMEDIATELY with the results.
            - MAINTAIN THE RANKING: The files in the context are numbered ([Rank: 1], [Rank: 2], etc.) from most affine to least affine. You MUST list them following this EXACT ascending numerical order. Do not group them and do not alter them.
            - DO NOT use greetings, pleasantries, or introductory phrases.
            - NEVER use emoticons or emojis.

            ANTI-HALLUCINATION AND CONTEXT RULES:
            1. Base yourself EXCLUSIVELY on the information contained in the "EXTRACTED CONTEXT". Do not use your general knowledge.
            2. DO NOT invent links not explicitly written.
            3. Analyze ALL documents provided in the context. 
            4. EVALUATE FOLDER WEIGHT: Autonomously distinguish between "Descriptive Folders" and "Mute/Administrative Folders".
               - Descriptive Folders: Indicate the nature of the file (e.g. "Instructions", "Maintenance"). USE THEM to deduce context.
               - Mute Folders: Organizational groupings (e.g. "Originals", "PDF", "Rev01"). IGNORE THEM completely.

            STRICTLY STRUCTURE YOUR RESPONSE IN TWO SECTIONS (copy these headers textually):

            RELEVANT DOCUMENTS:
            -[File Name] - Sec: [Section Name]
              Objectively summarize the content in one/two sentences.
              Provide extracted information answering the user's question.

            NON-RELEVANT DOCUMENTS:
            - [File Name] - Sec: [Section Name]: Explain in half a line why it was discarded.

            QUERY: {question}

            EXTRACTED CONTEXT:
            {context}"""
                chosen_model = getattr(self, 'response_model', 'gemini-1.5-pro')

                response = self.client.models.generate_content(
                    model=chosen_model,
                    contents=analyst_prompt,
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                response_text = response.text
                debug_info = self.track_usage("RAG Response", chosen_model, response)
                if debug_info:
                    response_text += f"\n\n{'-' * 40}\n{debug_info}"
            else:
                response_text = " AI Processing disabled. Semantic Engine active."

            self.root.after(0, lambda: self.update_nav_ui(response_text, sorted_files))

        except Exception as e:
            self.root.after(0, lambda: self.update_nav_ui(f"System Error: {e}", []))
        finally:
            def unlock():
                self.btn_nav_search.config(state="normal", text="PROCESS", bg=self.btn_bg_blue)
                self.ent_question.config(state="normal")
                self.ent_question.focus()

            self.root.after(0, unlock)

    def update_nav_ui(self, text, scored_files):
        self.txt_nav_response.config(state="normal")
        self.txt_nav_response.delete(1.0, tk.END)
        self.txt_nav_response.insert(tk.END, text)
        self.txt_nav_response.config(state="disabled")

        for w in self.f_nav_sources.winfo_children():
            w.destroy()

        if not scored_files:
            tk.Label(self.f_nav_sources, text="No associated files.", fg="#6B7280", bg=self.bg_color).pack(padx=10, pady=5, anchor="w")
            self._recalculate_scroll()
            return

        for relative_path, score in scored_files:
            percentage = int(score * 100)
            clean_name = os.path.basename(relative_path)

            btn_f = tk.Frame(self.f_nav_sources, bg="#FFFFFF", bd=1, relief="solid")
            btn_f.pack(fill="x", pady=4, padx=5)

            text_color = "#059669" if percentage >= 70 else "#D97706"
            label_text = f"📄 {clean_name} (Affinity: {percentage}%)"

            btn_container = tk.Frame(btn_f, bg="#FFFFFF")
            btn_container.pack(side="right", padx=10)

            tk.Button(btn_container, text="Open PDF", bg="#F3F4F6", fg="#111827", bd=1, relief="solid", cursor="hand2",
                      width=14, font=("Segoe UI", 11), command=lambda x=relative_path: self.open_file(x)).pack(side="left", padx=4)

            base = os.path.splitext(relative_path)[0]
            current_folder = os.path.normpath(self.folder_path.get())
            for ext in [".docx", ".doc"]:
                w_rel_path = base + ext
                w_full_path = os.path.join(current_folder, w_rel_path)
                if os.path.exists(w_full_path):
                    tk.Button(btn_container, text="Open Word", bg="#F3F4F6", fg="#111827", bd=1, relief="solid",
                              cursor="hand2", width=14, font=("Segoe UI", 11),
                              command=lambda x=w_rel_path: self.open_file(x)).pack(side="left", padx=4)

            tk.Label(btn_f, text=label_text, font=("Segoe UI", 12, "bold"), fg=text_color, bg="#FFFFFF",
                     anchor="w").pack(side="left", fill="x", expand=True, padx=10, pady=6)

        self._recalculate_scroll()

    def _recalculate_scroll(self):
        self.root.update_idletasks()
        h_real = self.f_nav_sources.winfo_reqheight()
        w_real = self.f_nav_sources.winfo_reqwidth()
        self.canvas_sources.configure(scrollregion=(0, 0, w_real, h_real))
        self.canvas_sources.yview_moveto(0)

    def open_file(self, relative_path):
        current_folder = os.path.normpath(self.folder_path.get())
        final_path = os.path.join(current_folder, relative_path)

        try:
            if sys.platform == "win32":
                os.startfile(final_path)
            elif sys.platform == "darwin":
                subprocess.call(["open", final_path])
            else:
                subprocess.call(["xdg-open", final_path])
        except Exception as e:
            messagebox.showerror("Error", f"Unable to open the file at:\n{final_path}\n\nDetail: {e}", parent=self.root)

    def request_interrupt(self):
        self.stop_flag.set()
        self.btn_stop_global.config(state="disabled", text="INTERRUPTION IN PROGRESS...", bg="#9CA3AF")
        self.log(self.log_ext, "⚠️ Interruption requested... Waiting for safe thread shutdown.")
        self.log(self.log_vet, "⚠️ Interruption requested... Waiting for safe thread shutdown.")

    # --- EXTRACTION LOGIC ---
    def run_extraction(self):
        if not self.check_prerequisites(check_folder=True): return
        if not SELECTED_EXTRACTION_FOLDERS:
            messagebox.showwarning("No source", "Select at least one folder from the tree in Settings.", parent=self.root)
            return

        self.stop_flag.clear()

        self.btn_ext_start.config(state="disabled")
        self.btn_vet_start.config(state="disabled")
        self.btn_stop_global.config(state="normal")

        self.lbl_eta_ext.config(text="Calculating ETA...")
        self.prog_ext['value'] = 0

        threading.Thread(target=self.extraction_thread, daemon=True).start()

    def extraction_thread(self):
        db = self.db_path.get()
        root_mother = os.path.normpath(self.folder_path.get())

        conn = get_db_connection(db)
        conn.execute("CREATE TABLE IF NOT EXISTS manual_extracts (id INTEGER PRIMARY KEY AUTOINCREMENT, original_file TEXT, macro_title TEXT, section TEXT, content TEXT, embedding_json TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS file_tracker (original_file TEXT PRIMARY KEY, modified_timestamp REAL, status TEXT)")
        self.log(self.log_ext, "Database alignment check in progress...")
        cursor = conn.cursor()
        cursor.execute("SELECT original_file FROM file_tracker")
        files_in_db = cursor.fetchall()

        deleted_files = 0
        for (rel_path,) in files_in_db:
            abs_path = os.path.join(root_mother, rel_path)
            if not os.path.exists(abs_path):
                cursor.execute("DELETE FROM manual_extracts WHERE original_file = ?", (rel_path,))
                cursor.execute("DELETE FROM file_tracker WHERE original_file = ?", (rel_path,))
                deleted_files += 1
                self.log(self.log_ext, f"🗑️ Removed orphan file from DB: {os.path.basename(rel_path)}")

        if deleted_files > 0:
            conn.commit()
            self.log(self.log_ext, f"Cleanup completed: {deleted_files} files removed from the database.")
        conn.close()
        absolute_path_set = set()

        for checked_folder in SELECTED_EXTRACTION_FOLDERS:
            if os.path.exists(checked_folder):
                try:
                    norm_folder = os.path.normpath(checked_folder)
                    if os.path.commonpath([root_mother, norm_folder]) != root_mother:
                        continue
                except ValueError:
                    continue

                for f in os.listdir(checked_folder):
                    if f.lower().endswith(".pdf") and not f.startswith("~$"):
                        p_abs = os.path.normpath(os.path.join(checked_folder, f))
                        absolute_path_set.add(p_abs)

        absolute_pdf_paths = list(absolute_path_set)
        total_files = len(absolute_pdf_paths)
        self.root.after(0, lambda: self.prog_ext.config(maximum=total_files if total_files > 0 else 1))

        if total_files == 0:
            self.log(self.log_ext, "No PDFs found in the selected folders.")
            self.root.after(0, lambda: self.lbl_eta_ext.config(text="No files processed"))
        else:
            self.log(self.log_ext, f"Starting processing on {total_files} PDFs found (Workers: {MAX_WORKERS})...")

            start_time = time.time()
            completed = 0
            total_run_cost = 0.0
            max_file_cost = 0.0
            files_with_cost = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(self.process_pdf, p, root_mother, db) for p in absolute_pdf_paths]
                for future in concurrent.futures.as_completed(futures):
                    if self.stop_flag.is_set():
                        break
                    try:
                        file_cost = future.result()
                        if file_cost and file_cost > 0:
                            total_run_cost += file_cost
                            if file_cost > max_file_cost:
                                max_file_cost = file_cost
                            files_with_cost += 1
                    except Exception:
                        pass
                    completed += 1
                    elapsed = max(time.time() - start_time, 0.1)
                    speed = completed / elapsed
                    remaining = total_files - completed
                    eta_sec = remaining / speed
                    eta_str = self.format_eta(eta_sec)

                    self.root.after(0, lambda c=completed, t=total_files, e=eta_str: self.update_prog_ext(c, t, e))
            if self.use_debug_var.get() and files_with_cost > 0:
                avg_cost = total_run_cost / files_with_cost
                report = f"\n📊 EXTRACTION COST REPORT:\n- Total: ${total_run_cost:.6f}\n- Average cost per file: ${avg_cost:.6f}\n- MAX single file cost: ${max_file_cost:.6f}\n{'-' * 40}"
                self.log(self.log_ext, report)
            if self.stop_flag.is_set():
                self.log(self.log_ext, "Operation interrupted by system.")
                self.root.after(0, lambda: self.lbl_eta_ext.config(text="Interrupted"))
            else:
                self.log(self.log_ext, "Data extraction completed successfully.")
                self.root.after(0, lambda: self.lbl_eta_ext.config(text="Completed"))
                self.root.after(0, self.check_synchronization)

        self.root.after(0, lambda: self.btn_ext_start.config(state="normal"))
        self.root.after(0, lambda: self.btn_vet_start.config(state="normal"))
        self.root.after(0, lambda: self.btn_stop_global.config(state="disabled", text="INTERRUPT CURRENT PROCESS", bg=self.btn_bg_red))

    def process_pdf(self, abs_path, root_folder, db):
        if self.stop_flag.is_set(): return 0.0
        total_file_cost = 0.0

        relative_path = safe_relative_path(abs_path, root_folder)
        mtime = os.path.getmtime(abs_path)

        base_path = os.path.splitext(abs_path)[0]
        reference_mtime = mtime
        word_file_found = None
        for ext in [".docx", ".doc"]:
            word_path = base_path + ext
            if os.path.exists(word_path):
                word_file_found = word_path
                word_mtime = os.path.getmtime(word_path)
                if word_mtime > mtime:
                    reference_mtime = word_mtime
                break
        with self.lock_db:
            conn = get_db_connection(db)
            cursor = conn.cursor()
            cursor.execute("SELECT modified_timestamp, status FROM file_tracker WHERE original_file=?", (relative_path,))
            row = cursor.fetchone()

            if row:
                db_mtime, status = row
                if status == 'COMPLETED' and db_mtime == reference_mtime:
                    self.log(self.log_ext, f"Already updated: {relative_path}")
                    conn.close()
                    return
                else:
                    self.log(self.log_ext, f"Updating: {relative_path}...")
                    cursor.execute("DELETE FROM manual_extracts WHERE original_file=?", (relative_path,))
                    cursor.execute("UPDATE file_tracker SET status='IN_PROGRESS', modified_timestamp=? WHERE original_file=?", (mtime, relative_path))
            else:
                self.log(self.log_ext, f"New file: {relative_path}")
                cursor.execute("INSERT INTO file_tracker (original_file, modified_timestamp, status) VALUES (?, ?, 'IN_PROGRESS')", (relative_path, mtime))

            conn.commit()
            conn.close()

        import pypdf
        try:
            reader = pypdf.PdfReader(abs_path)

            if reader.is_encrypted:
                try:
                    reader.decrypt('')
                except Exception:
                    pass

            try:
                pages = len(reader.pages)
            except Exception as e:
                raise Exception("File locked by DRM or Advanced Encryption (Protected Standards/Normatives).")

            for i in range(0, pages, MAX_PAGES_PER_BLOCK):
                if self.stop_flag.is_set(): break
                writer = pypdf.PdfWriter()
                for j in range(i, min(i + MAX_PAGES_PER_BLOCK, pages)):
                    writer.add_page(reader.pages[j])

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp)
                    tmp_path = tmp.name

                max_attempts = 5
                success = False

                for attempt in range(max_attempts):
                    try:
                        f_cloud = self.client.files.upload(file=tmp_path)
                        chosen_model = getattr(self, 'extraction_model', 'gemini-1.5-flash')
                        resp = self.client.models.generate_content(
                            model=chosen_model,
                            contents=[f_cloud, EXTRACTION_PROMPT],
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                max_output_tokens=8192
                            )
                        )
                        raw_text = resp.text
                        _, cost = self.track_usage(f"OCR {os.path.basename(abs_path)}", chosen_model, resp, self.log_ext)
                        total_file_cost += cost
                        try:
                            data = json.loads(raw_text)
                        except Exception as json_err:
                            if "escape" in str(json_err).lower():
                                clean_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw_text)
                                data = json.loads(clean_text)
                            else:
                                raise json_err

                        with self.lock_db:
                            c = get_db_connection(db)
                            for b in data:
                                c.execute(
                                    "INSERT INTO manual_extracts (original_file, macro_title, section, content) VALUES (?,?,?,?)",
                                    (relative_path, b.get('macro_title', ''), b.get('section', ''), b.get('text', '')))
                            c.commit()
                            c.close()

                        self.client.files.delete(name=f_cloud.name)
                        success = True
                        break
                    except Exception as e:
                        if "429" in str(e):
                            time.sleep(5 * (2 ** attempt))
                        else:
                            raise e
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)

                if not success:
                    raise Exception(f"Failed after {max_attempts} attempts.")

            if not self.stop_flag.is_set():
                with self.lock_db:
                    conn = get_db_connection(db)
                    conn.execute("UPDATE file_tracker SET status='COMPLETED' WHERE original_file=?", (relative_path,))
                    conn.commit()
                    conn.close()
                self.log(self.log_ext, f"✔ Done: {os.path.basename(relative_path)}")

        except Exception as e:
            self.log(self.log_ext, f"❌ Error on {os.path.basename(relative_path)}: {e}")
            with self.lock_db:
                conn = get_db_connection(db)
                conn.execute("UPDATE file_tracker SET status='ERROR' WHERE original_file=?", (relative_path,))
                conn.commit()
                conn.close()
        return total_file_cost

    # --- VECTORIZATION LOGIC ---
    def enrich_and_chunk_database(self):
        db = self.db_path.get()
        if not db or not os.path.exists(db): return

        MAX_CHAR_CHUNK = 3000
        OVERLAP = 300

        try:
            conn = get_db_connection(db)

            conn.execute("PRAGMA cache_size = -20000")
            cursor = conn.cursor()

            cursor.execute("SELECT DISTINCT original_file FROM manual_extracts WHERE embedding_json IS NULL OR content NOT LIKE '[DOC CONTEXT:%'")
            file_list = [r[0] for r in cursor.fetchall()]

            for org_file in file_list:
                if self.stop_flag.is_set(): break

                cursor.execute("""
                               SELECT macro_title, content
                               FROM manual_extracts
                               WHERE original_file = ?
                                 AND (section LIKE '%scope%' OR section LIKE '%field of application%' OR
                                      section LIKE '%objective%')
                                   LIMIT 1
                               """, (org_file,))
                scope_row = cursor.fetchone()

                cursor.execute("SELECT macro_title FROM manual_extracts WHERE original_file = ? ORDER BY id LIMIT 1", (org_file,))
                first_title_row = cursor.fetchone()

                doc_title = first_title_row[0] if (first_title_row and first_title_row[0]) else "Document"
                scope_text = ""
                if scope_row:
                    doc_title = scope_row[0] or doc_title
                    scope_text = scope_row[1] or ""

                cursor.execute("SELECT id FROM manual_extracts WHERE original_file = ? ORDER BY id", (org_file,))
                ids_to_process = [r[0] for r in cursor.fetchall()]

                for index, row_id in enumerate(ids_to_process):
                    if self.stop_flag.is_set(): break

                    cursor.execute("SELECT macro_title, section, content FROM manual_extracts WHERE id = ?", (row_id,))
                    row_data = cursor.fetchone()
                    if not row_data: continue

                    macro, sec, cont = row_data

                    if cont and "[DOC CONTEXT:" in cont:
                        del row_data
                        continue

                    m_safe = macro or doc_title
                    s_safe = sec or "Section"
                    c_safe = cont or ""

                    if scope_text and c_safe != scope_text:
                        scope_prefix = (scope_text[:200] + "...") if len(scope_text) > 200 else scope_text
                        stamp = f"[DOC CONTEXT: {m_safe} | SCOPE: {scope_prefix}]"
                    else:
                        stamp = f"[DOC CONTEXT: {m_safe}]"

                    full_text = f"{stamp}\n{c_safe}"
                    chunks_to_insert = []

                    if len(full_text) > MAX_CHAR_CHUNK:
                        start = 0
                        idx = 1
                        while start < len(full_text):
                            end = min(start + MAX_CHAR_CHUNK, len(full_text))

                            if end < len(full_text):
                                last_space = full_text.rfind(' ', start + 2500, end)
                                if last_space != -1: end = last_space

                            chunks_to_insert.append((org_file, m_safe, f"{s_safe} p.{idx}", full_text[start:end]))

                            if end == len(full_text):
                                break

                            start = end - OVERLAP
                            idx += 1
                    else:
                        chunks_to_insert.append((org_file, m_safe, s_safe, full_text))

                    cursor.executemany(
                        "INSERT INTO manual_extracts (original_file, macro_title, section, content) VALUES (?,?,?,?)",
                        chunks_to_insert
                    )
                    cursor.execute("DELETE FROM manual_extracts WHERE id = ?", (row_id,))

                    del row_data, full_text, c_safe, chunks_to_insert

                    if index % 20 == 0:
                        conn.commit()

                conn.commit()

            conn.close()

            import gc
            gc.collect()

        except Exception as e:
            self.log(self.log_vet, f"❌ Critical RAM/DB Error: {e}")

    def run_vectorization(self):
        if not self.check_prerequisites(): return

        self.stop_flag.clear()

        self.btn_vet_start.config(state="disabled")
        self.btn_ext_start.config(state="disabled")
        self.btn_stop_global.config(state="normal")

        self.lbl_eta_vet.config(text="Calculating ETA...")
        self.prog_vet['value'] = 0

        threading.Thread(target=self.vectorization_thread, daemon=True).start()

    def vectorization_thread(self):
        db = self.db_path.get()
        self.root.after(0, lambda: self.lbl_eta_vet.config(text="Data preparation in progress..."))
        self.log(self.log_vet, "Injecting metadata and chunking long sections...")
        self.enrich_and_chunk_database()

        if self.stop_flag.is_set():
            self.root.after(0, lambda: self.lbl_eta_vet.config(text="Interrupted"))
            self.root.after(0, lambda: self.btn_ext_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_vet_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_stop_global.config(state="disabled", text="INTERRUPT CURRENT PROCESS", bg=self.btn_bg_red))
            return

        try:
            conn = get_db_connection(db)
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='manual_extracts'")
            if not cursor.fetchone():
                self.log(self.log_vet, "Database lacks indexes. Preliminary extraction required.")
                conn.close()
                self.root.after(0, lambda: self.btn_ext_start.config(state="normal"))
                self.root.after(0, lambda: self.btn_vet_start.config(state="normal"))
                self.root.after(0, lambda: self.btn_stop_global.config(state="disabled"))
                self.root.after(0, lambda: self.lbl_eta_vet.config(text="No data"))
                return

            cursor.execute("SELECT id, macro_title, section, content FROM manual_extracts WHERE embedding_json IS NULL")
            to_do = cursor.fetchall()
            conn.close()

            total_chunks = len(to_do)
            self.root.after(0, lambda: self.prog_vet.config(maximum=total_chunks if total_chunks > 0 else 1))

            if total_chunks == 0:
                self.log(self.log_vet, "✅ Operation unnecessary: Database perfectly updated.")
                self.root.after(0, lambda: self.lbl_eta_vet.config(text="Database Updated"))
            else:
                self.log(self.log_vet, f"🚀 Starting calculation on {total_chunks} rows...")

                start_time = time.time()
                completed = 0
                total_vet_cost = 0.0

                def process_single_vector(row):
                    if self.stop_flag.is_set(): return 0.0
                    db_id, macro_title, section, content = row
                    text = f"{macro_title} {section} {content}"

                    try:
                        resp = self.client.models.embed_content(
                            model=self.embed_model,
                            contents=text
                        )
                        vector_json = json.dumps(resp.embeddings[0].values)
                        _, cost = self.track_usage("Embed", self.embed_model, resp, None)

                        with self.lock_db:
                            c = get_db_connection(db)
                            c.execute("UPDATE manual_extracts SET embedding_json=? WHERE id=?", (vector_json, db_id))
                            c.commit()
                            c.close()

                        return cost
                    except Exception as e:
                        self.log(self.log_vet, f"❌ API Error on ID {db_id}: {e}")
                        return 0.0

                with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                    futures = [executor.submit(process_single_vector, row) for row in to_do]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            total_vet_cost += future.result()
                        except Exception as crash:
                            self.log(self.log_vet, f"❌ INTERNAL THREAD CRASH: {crash}")

                        completed += 1

                        elapsed = max(time.time() - start_time, 0.1)
                        speed = completed / elapsed
                        remaining = total_chunks - completed
                        eta_sec = remaining / speed if speed > 0 else 0
                        eta_str = self.format_eta(eta_sec)

                        self.root.after(0, lambda c=completed, t=total_chunks, e=eta_str: self.update_prog_vet(c, t, e))

                if self.use_debug_var.get() and total_vet_cost > 0:
                    self.log(self.log_vet, f"\n📊 TOTAL EMBEDDING COST: ${total_vet_cost:.6f}\n{'-' * 40}")

                if self.stop_flag.is_set():
                    self.log(self.log_vet, "Operation interrupted.")
                    self.root.after(0, lambda: self.lbl_eta_vet.config(text="Interrupted"))
                else:
                    self.log(self.log_vet, "✅ Vectorization completed. All rows have been processed.")
                    self.root.after(0, lambda: self.lbl_eta_vet.config(text="Completed"))

        except Exception as fatal:
            self.log(self.log_vet, f"❌ FATAL ERROR: {fatal}")

        finally:
            self.root.after(0, lambda: self.btn_ext_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_vet_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_stop_global.config(state="disabled", text="INTERRUPT CURRENT PROCESS", bg=self.btn_bg_red))
            self.root.after(0, self.check_synchronization)


if __name__ == "__main__":
    print("[2] Global space read successfully! Initializing Tkinter...")
    root = tk.Tk()
    print("[3] Base Windows window created.")
    try:
        root.iconbitmap(resource_path("icon.ico"))
    except:
        pass
    print("[4] Drawing buttons and interface...")
    app = AIGuideSystem(root)
    print("[5] Interface built. Starting graphic engine!")
    root.mainloop()
