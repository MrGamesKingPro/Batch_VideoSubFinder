import customtkinter as ctk
from tkinter import filedialog, messagebox, Toplevel
import configparser
import subprocess
import os
import sys
from pathlib import Path
from time import time as perf_time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import queue
import time
import json # For ffprobe output
import traceback # For detailed error logging

# --- Pillow and OpenCV ---
try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    messagebox.showerror("Dependency Error", "Pillow library is not installed. Please install it (pip install Pillow).")
    sys.exit(1)

try:
    import cv2
except ImportError:
    messagebox.showerror("Dependency Error", "OpenCV library (cv2) is not installed. Please install it (pip install opencv-python).")
    sys.exit(1)

# --- Constants ---
SETTINGS_FILE = "Settings.ini"
DEFAULT_OUTPUT_RELPATH = "Output_Videos_Images"
APP_NAME = "Batch_VideoSubFinder V2.0.3"
VERSION_INFO = "Programmed by Youtube@MrGamesKingPro"
VIDEO_FILE_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"} # Common video extensions

# --- FFprobe Path ---
FFPROBE_PATH = "ffmpeg/ffprobe.exe" # Ensure ffprobe is in system PATH or provide full path

# --- Default Settings (if Settings.ini is missing) ---
DEFAULT_SETTINGS = {
    "Path": {
        "videosubfinder_path": "VideoSubFinderWXW.exe",
        "Videos_path": "Paste_Multi_Videos_Here",
        "general_settings": "general.cfg", # Default name for general.cfg
        "output_path": DEFAULT_OUTPUT_RELPATH,
    },
    "Settings": {
        "mode_open_video": "-ovffmpeg",
        "create_cleared_text_images": "-ccti",
        "start_time": "",
        "end_time": "",
        "use_cuda": "",
        "number_threads_rgbimages": "",
        "number_threads_txtimages": "",
    }
}

# Default values for general.cfg crop settings (VSF standard)
DEFAULT_CROP_SETTINGS = {
    "top_video_image_percent_end": "0.258929",
    "bottom_video_image_percent_end": "0",
    "left_video_image_percent_end": "0",
    "right_video_image_percent_end": "1",
}
# Order for adding to a new/empty general.cfg
CROP_SETTING_KEYS_ORDER = [
    "top_video_image_percent_end",
    "bottom_video_image_percent_end",
    "left_video_image_percent_end",
    "right_video_image_percent_end",
]

# --- Helper: Get base path for resources (for PyInstaller) ---
def get_base_path():
    if getattr(sys, 'frozen', False): # PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).parent # Script execution

BASE_PATH = get_base_path()

# --- VideoFrameLabelCTK: Handles visual crop line display and interaction ---
class VideoFrameLabelCTK:
    def __init__(self, master_widget, width, height, lines_changed_callback):
        self.master_widget = master_widget
        self.lines_changed_callback = lines_changed_callback

        self.label = ctk.CTkLabel(master_widget, text="", width=width, height=height)
        self.label.pack(fill="both", expand=True, padx=5, pady=5)

        self.widget_width = width
        self.widget_height = height
        self.video_width = 0
        self.video_height = 0
        self.scaled_image_width = 0
        self.scaled_image_height = 0
        self.offset_x = 0
        self.offset_y = 0

        self.current_pil_image = None
        self.display_ctk_image = None

        self.line_top_y_vid = 0
        self.line_bottom_y_vid = 0
        self.line_left_x_vid = 0
        self.line_right_x_vid = 0

        default_crop_float = {k: float(v) for k, v in DEFAULT_CROP_SETTINGS.items()}
        self.initial_percentages_ini_style = default_crop_float # Store VSF INI style percentages

        self.dragging_line = None
        self.grab_margin = 10

        self.label.bind("<ButtonPress-1>", self._mouse_press)
        self.label.bind("<B1-Motion>", self._mouse_move)
        self.label.bind("<ButtonRelease-1>", self._mouse_release)
        self.label.bind("<Motion>", self._mouse_hover_cursor)
        self.label.bind("<Configure>", self._on_label_configure)

        # Initialize with no image
        self.set_pil_image(None)


    def _on_label_configure(self, event):
        if self.widget_width != event.width or self.widget_height != event.height:
            self.widget_width = event.width
            self.widget_height = event.height
            # Only redraw if an image actually exists
            if self.current_pil_image:
                self.set_pil_image(self.current_pil_image)
            else: # If no image, resize the placeholder
                 self.set_pil_image(None)

    def set_video_properties(self, video_width, video_height):
        # print(f"DEBUG: Setting video properties: {video_width}x{video_height}")
        self.video_width = video_width
        self.video_height = video_height
        # Re-apply the stored percentages when video dimensions are known/changed
        self.apply_percentages_ini_style(self.initial_percentages_ini_style, emit_change=False)
        # Re-draw if an image is currently loaded
        if self.current_pil_image:
            self.set_pil_image(self.current_pil_image)
        # print(f"DEBUG: Applied percentages. Lines Vid Coords: T={self.line_top_y_vid}, B={self.line_bottom_y_vid}, L={self.line_left_x_vid}, R={self.line_right_x_vid}")

    def _calculate_display_geometry(self, pil_image_w, pil_image_h):
        if self.widget_width <= 0 or self.widget_height <= 0 or pil_image_w <= 0 or pil_image_h <= 0:
            self.scaled_image_width, self.scaled_image_height = pil_image_w, pil_image_h # Or some defaults
            self.offset_x, self.offset_y = 0, 0
            if self.widget_width > 0 and self.widget_height > 0: # If label has size, use it
                 self.scaled_image_width, self.scaled_image_height = max(1, self.widget_width), max(1, self.widget_height)
            return

        label_aspect = self.widget_width / self.widget_height
        image_aspect = pil_image_w / pil_image_h

        if label_aspect > image_aspect:
            self.scaled_image_height = self.widget_height
            self.scaled_image_width = int(self.scaled_image_height * image_aspect)
        else:
            self.scaled_image_width = self.widget_width
            self.scaled_image_height = int(self.scaled_image_width / image_aspect)

        self.scaled_image_width = max(1, self.scaled_image_width) # Ensure not zero
        self.scaled_image_height = max(1, self.scaled_image_height)


        self.offset_x = (self.widget_width - self.scaled_image_width) // 2
        self.offset_y = (self.widget_height - self.scaled_image_height) // 2

    def set_pil_image(self, pil_image):
        # print(f"DEBUG: set_pil_image called. Has image: {pil_image is not None}. Video dims: {self.video_width}x{self.video_height}")
        self.current_pil_image = pil_image
        if not pil_image:
            # Create placeholder if widget has size
            placeholder_w = max(1, self.widget_width)
            placeholder_h = max(1, self.widget_height)
            img = Image.new("RGB", (placeholder_w, placeholder_h), "black")
            draw = ImageDraw.Draw(img)
            try: font = ImageFont.truetype("arial.ttf", 20)
            except IOError: font = ImageFont.load_default()
            text = "No video loaded / Seek to display frame"

            text_bbox = draw.textbbox((0,0), text, font=font) if hasattr(draw, 'textbbox') else (0,0,0,0) # Basic fallback
            if text_bbox == (0,0,0,0) and hasattr(draw, 'textlength'): # PIL < 10
                 try:
                     text_w = draw.textlength(text, font=font)
                     text_h = font.getsize(text)[1] if hasattr(font, 'getsize') else 20
                 except AttributeError:
                     text_w, text_h = 100, 20 # Fallback size
            elif text_bbox != (0,0,0,0):
                 text_w = text_bbox[2] - text_bbox[0]
                 text_h = text_bbox[3] - text_bbox[1]
            else: # Absolute fallback
                 text_w, text_h = 100, 20

            text_x = max(0, (placeholder_w - text_w) // 2)
            text_y = max(0, (placeholder_h - text_h) // 2)
            draw.text((text_x, text_y), text, fill="white", font=font)

            self.display_ctk_image = ctk.CTkImage(light_image=img, dark_image=img, size=(placeholder_w, placeholder_h))
            self.label.configure(image=self.display_ctk_image, text="")
            self.label.image = self.display_ctk_image # Keep reference
            # print("DEBUG: Placeholder set")
            return

        # If we have an image but no video properties set yet, derive from image
        if self.video_width == 0 or self.video_height == 0:
             # print("DEBUG: Setting video properties from first loaded image")
             self.set_video_properties(pil_image.width, pil_image.height)

        # Proceed only if video dimensions are known
        if self.video_width == 0 or self.video_height == 0:
            print("ERROR: Video dimensions still 0, cannot proceed with drawing.")
            return

        self._calculate_display_geometry(pil_image.width, pil_image.height)

        img_for_display = self.current_pil_image.copy()
        try:
            img_for_display = img_for_display.resize((self.scaled_image_width, self.scaled_image_height), Image.Resampling.LANCZOS)
        except AttributeError: # Older Pillow
            img_for_display = img_for_display.resize((self.scaled_image_width, self.scaled_image_height), Image.LANCZOS)

        draw = ImageDraw.Draw(img_for_display)

        # Draw lines only if video dimensions are valid
        if self.video_width > 0 and self.video_height > 0 and self.scaled_image_width > 0 and self.scaled_image_height > 0:
            scale_x_factor = self.scaled_image_width / self.video_width
            scale_y_factor = self.scaled_image_height / self.video_height

            # print(f"DEBUG: Drawing lines. Scale factors: x={scale_x_factor:.2f}, y={scale_y_factor:.2f}")
            # print(f"DEBUG: Vid Coords: T={self.line_top_y_vid}, B={self.line_bottom_y_vid}, L={self.line_left_x_vid}, R={self.line_right_x_vid}")

            disp_top_y = int(self.line_top_y_vid * scale_y_factor)
            disp_bottom_y = int(self.line_bottom_y_vid * scale_y_factor)
            disp_left_x = int(self.line_left_x_vid * scale_x_factor)
            disp_right_x = int(self.line_right_x_vid * scale_x_factor)

            # Clamp display coordinates to be within the scaled image boundaries
            disp_top_y = max(0, min(disp_top_y, self.scaled_image_height - 1))
            disp_bottom_y = max(0, min(disp_bottom_y, self.scaled_image_height - 1))
            disp_left_x = max(0, min(disp_left_x, self.scaled_image_width - 1))
            disp_right_x = max(0, min(disp_right_x, self.scaled_image_width - 1))

            # print(f"DEBUG: Disp Coords: T={disp_top_y}, B={disp_bottom_y}, L={disp_left_x}, R={disp_right_x}")

            draw.line([(0, disp_top_y), (self.scaled_image_width -1, disp_top_y)], fill="lime", width=2)
            draw.line([(0, disp_bottom_y), (self.scaled_image_width-1, disp_bottom_y)], fill="lime", width=2)
            draw.line([(disp_left_x, 0), (disp_left_x, self.scaled_image_height-1)], fill="lime", width=2)
            draw.line([(disp_right_x, 0), (disp_right_x, self.scaled_image_height-1)], fill="lime", width=2)

        self.display_ctk_image = ctk.CTkImage(light_image=img_for_display, dark_image=img_for_display,
                                              size=(self.scaled_image_width, self.scaled_image_height))
        self.label.configure(image=self.display_ctk_image, text="")
        self.label.image = self.display_ctk_image # Keep reference
        # print("DEBUG: Image with lines set")

    def _widget_to_video_coords(self, widget_x, widget_y):
        if self.scaled_image_width == 0 or self.scaled_image_height == 0: return 0, 0
        img_x = widget_x - self.offset_x
        img_y = widget_y - self.offset_y
        img_x = max(0, min(img_x, self.scaled_image_width -1))
        img_y = max(0, min(img_y, self.scaled_image_height -1))
        vid_x = int((img_x / self.scaled_image_width) * self.video_width)
        vid_y = int((img_y / self.scaled_image_height) * self.video_height)
        return vid_x, vid_y

    def _get_line_rect_widget_coords(self, line_type):
        if not self.current_pil_image or self.video_width == 0 or self.video_height == 0 or self.scaled_image_width == 0 or self.scaled_image_height == 0: return (0,0,0,0)
        scale_x_factor = self.scaled_image_width / self.video_width
        scale_y_factor = self.scaled_image_height / self.video_height

        if line_type == "top":
            y_disp = int(self.line_top_y_vid * scale_y_factor) + self.offset_y
            return (self.offset_x, y_disp - self.grab_margin // 2, self.scaled_image_width, self.grab_margin)
        elif line_type == "bottom":
            y_disp = int(self.line_bottom_y_vid * scale_y_factor) + self.offset_y
            return (self.offset_x, y_disp - self.grab_margin // 2, self.scaled_image_width, self.grab_margin)
        elif line_type == "left":
            x_disp = int(self.line_left_x_vid * scale_x_factor) + self.offset_x
            return (x_disp - self.grab_margin // 2, self.offset_y, self.grab_margin, self.scaled_image_height)
        elif line_type == "right":
            x_disp = int(self.line_right_x_vid * scale_x_factor) + self.offset_x
            return (x_disp - self.grab_margin // 2, self.offset_y, self.grab_margin, self.scaled_image_height)
        return (0,0,0,0)

    def _is_point_in_rect(self, x, y, rect):
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def _mouse_press(self, event):
        if not self.current_pil_image or self.video_width == 0 or self.video_height == 0: return
        self.dragging_line = None
        if self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("top")): self.dragging_line = "top"
        elif self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("bottom")): self.dragging_line = "bottom"
        elif self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("left")): self.dragging_line = "left"
        elif self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("right")): self.dragging_line = "right"

    def _mouse_move(self, event):
        if not self.dragging_line or not self.current_pil_image:
            self._mouse_hover_cursor(event)
            return

        vid_x, vid_y = self._widget_to_video_coords(event.x, event.y)
        cursor_map = {"top": "sb_v_double_arrow", "bottom": "sb_v_double_arrow",
                      "left": "sb_h_double_arrow", "right": "sb_h_double_arrow"}
        self.label.configure(cursor=cursor_map.get(self.dragging_line, "arrow"))

        # Ensure coordinates stay within video bounds and lines don't cross
        vh_m1 = self.video_height - 1 if self.video_height > 0 else 0
        vw_m1 = self.video_width - 1 if self.video_width > 0 else 0

        if self.dragging_line == "top":
            self.line_top_y_vid = max(0, min(vid_y, self.line_bottom_y_vid - 1, vh_m1))
        elif self.dragging_line == "bottom":
            self.line_bottom_y_vid = max(self.line_top_y_vid + 1, min(vid_y, vh_m1))
        elif self.dragging_line == "left":
            self.line_left_x_vid = max(0, min(vid_x, self.line_right_x_vid - 1, vw_m1))
        elif self.dragging_line == "right":
            self.line_right_x_vid = max(self.line_left_x_vid + 1, min(vid_x, vw_m1))

        self._ensure_lines_valid_video_coords() # Redundant check, but safe
        self.set_pil_image(self.current_pil_image) # Redraw with new line positions
        self._emit_lines_changed()

    def _mouse_hover_cursor(self, event):
        if self.dragging_line: return
        if not self.current_pil_image or self.video_width == 0 or self.video_height == 0:
            self.label.configure(cursor="arrow"); return

        if self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("top")) or \
           self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("bottom")):
            self.label.configure(cursor="sb_v_double_arrow")
        elif self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("left")) or \
             self._is_point_in_rect(event.x, event.y, self._get_line_rect_widget_coords("right")):
            self.label.configure(cursor="sb_h_double_arrow")
        else: self.label.configure(cursor="arrow")

    def _mouse_release(self, event):
        if self.dragging_line and self.current_pil_image:
            self._emit_lines_changed()
        self.dragging_line = None
        self.label.configure(cursor="arrow")


    def _ensure_lines_valid_video_coords(self):
        # Return early if video dimensions aren't set
        if self.video_width <= 0 or self.video_height <= 0: return

        vh_m1 = self.video_height - 1
        vw_m1 = self.video_width - 1

        # Ensure individual lines are within bounds
        self.line_top_y_vid = max(0, min(self.line_top_y_vid, vh_m1))
        self.line_bottom_y_vid = max(0, min(self.line_bottom_y_vid, vh_m1))
        self.line_left_x_vid = max(0, min(self.line_left_x_vid, vw_m1))
        self.line_right_x_vid = max(0, min(self.line_right_x_vid, vw_m1))

        # Ensure lines haven't crossed and maintain minimum 1px separation
        if self.line_top_y_vid >= self.line_bottom_y_vid:
             self.line_top_y_vid = max(0, self.line_bottom_y_vid - 1)
             # Re-check bottom if top was pushed against it at the top edge
             self.line_bottom_y_vid = max(self.line_top_y_vid + 1, self.line_bottom_y_vid)

        if self.line_left_x_vid >= self.line_right_x_vid:
            self.line_left_x_vid = max(0, self.line_right_x_vid - 1)
            # Re-check right if left was pushed against it at the left edge
            self.line_right_x_vid = max(self.line_left_x_vid + 1, self.line_right_x_vid)


    def _emit_lines_changed(self):
        percentages = self.get_current_percentages_ini_style()
        if self.lines_changed_callback:
            self.lines_changed_callback(percentages)

    def apply_percentages_ini_style(self, percentages_ini_style, emit_change=True):
        # Store the desired percentages
        self.initial_percentages_ini_style = {k: float(v) for k, v in percentages_ini_style.items()}
        # print(f"DEBUG: Applying percentages: {self.initial_percentages_ini_style}")

        # Apply them only if video dimensions are known
        if self.video_width > 0 and self.video_height > 0:
            ini_top = self.initial_percentages_ini_style.get('top_video_image_percent_end', 0.0)
            ini_bottom = self.initial_percentages_ini_style.get('bottom_video_image_percent_end', 0.0)
            ini_left = self.initial_percentages_ini_style.get('left_video_image_percent_end', 0.0)
            ini_right = self.initial_percentages_ini_style.get('right_video_image_percent_end', 1.0)

            # VSF % end defines the *end* of the area to *keep*.
            # Visual Top Line Y = (1.0 - %top_end) * Height
            # Visual Bottom Line Y = (1.0 - %bottom_end) * Height
            # Visual Left Line X = %left_end * Width
            # Visual Right Line X = %right_end * Width

            visual_top_frac = 1.0 - ini_top
            visual_bottom_frac = 1.0 - ini_bottom
            visual_left_frac = ini_left
            visual_right_frac = ini_right

            self.line_top_y_vid = int(visual_top_frac * self.video_height)
            self.line_bottom_y_vid = int(visual_bottom_frac * self.video_height)
            self.line_left_x_vid = int(visual_left_frac * self.video_width)
            self.line_right_x_vid = int(visual_right_frac * self.video_width)

            self._ensure_lines_valid_video_coords()
            # print(f"DEBUG: Calculated line coords from %: T={self.line_top_y_vid}, B={self.line_bottom_y_vid}, L={self.line_left_x_vid}, R={self.line_right_x_vid}")

            if self.current_pil_image:
                self.set_pil_image(self.current_pil_image) # Redraw with new lines if image exists
            if emit_change:
                self._emit_lines_changed()
        elif self.current_pil_image is None:
            # If no video dimensions yet and no image, ensure placeholder is shown
            self.set_pil_image(None)


    def get_current_percentages_ini_style(self):
        if self.video_width == 0 or self.video_height == 0:
            # Return the initially set/stored percentages if video isn't loaded
            return self.initial_percentages_ini_style.copy()

        vis_top_p = self.line_top_y_vid / self.video_height
        vis_bottom_p = self.line_bottom_y_vid / self.video_height
        vis_left_p = self.line_left_x_vid / self.video_width
        vis_right_p = self.line_right_x_vid / self.video_width

        # Convert back to VSF format
        ini_top_p = 1.0 - vis_top_p
        ini_bottom_p = 1.0 - vis_bottom_p
        ini_left_p = vis_left_p
        ini_right_p = vis_right_p

        return {
            'top_video_image_percent_end': round(ini_top_p, 7),
            'bottom_video_image_percent_end': round(ini_bottom_p, 7),
            'left_video_image_percent_end': round(ini_left_p, 7),
            'right_video_image_percent_end': round(ini_right_p, 7),
        }

# --- CropRegionEditorWindow ---
class CropRegionEditorWindow(ctk.CTkToplevel):
    def __init__(self, master, general_cfg_path_var, video_input_folder_var, main_app_refresh_callback, initial_crop_settings):
        super().__init__(master)
        self.title("Visual Crop Region Editor")
        self.geometry("900x750")
        self.transient(master)
        self.grab_set()

        self.general_cfg_path_var = general_cfg_path_var
        self.video_input_folder_var = video_input_folder_var
        self.main_app_refresh_callback = main_app_refresh_callback
        self.initial_crop_settings_from_main_app = initial_crop_settings

        self.video_path = None
        self.cap = None
        self.video_fps = 0
        self.video_total_frames = 0
        self.video_duration_ms = 0
        self.video_width = 0
        self.video_height = 0

        self.current_crop_percentages_ini_style = self.initial_crop_settings_from_main_app.copy()

        self._init_ui()
        self.video_frame_widget.apply_percentages_ini_style(self.current_crop_percentages_ini_style, emit_change=False)
        self.display_percentage_labels(self.current_crop_percentages_ini_style)

        self.after(50, self._try_load_first_video)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_ui(self):
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(padx=10, pady=10, fill="both", expand=True)

        video_area_frame = ctk.CTkFrame(main_frame)
        video_area_frame.pack(padx=5, pady=5, fill="both", expand=True)
        self.video_frame_widget = VideoFrameLabelCTK(video_area_frame, 640, 360, self.update_percentage_labels_and_storage)

        time_layout = ctk.CTkFrame(main_frame)
        time_layout.pack(fill="x", padx=5, pady=5)
        self.current_time_label = ctk.CTkLabel(time_layout, text="00:00.000", width=80)
        self.current_time_label.pack(side="left", padx=5)
        self.time_slider = ctk.CTkSlider(time_layout, from_=0, to=100, command=self.slider_seek_handler)
        self.time_slider.set(0)
        self.time_slider.configure(state="disabled")
        self.time_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.total_time_label = ctk.CTkLabel(time_layout, text="00:00.000", width=80)
        self.total_time_label.pack(side="left", padx=5)

        perc_frame = ctk.CTkFrame(main_frame)
        perc_frame.pack(fill="x", padx=5, pady=5)
        perc_frame.grid_columnconfigure((0,1,2,3), weight=1)
        self.top_perc_label = ctk.CTkLabel(perc_frame, text="Crop Top: N/A")
        self.top_perc_label.grid(row=0, column=0, padx=2,pady=2, sticky="w")
        self.bottom_perc_label = ctk.CTkLabel(perc_frame, text="Crop Bottom: N/A")
        self.bottom_perc_label.grid(row=0, column=1, padx=2,pady=2, sticky="w")
        self.left_perc_label = ctk.CTkLabel(perc_frame, text="Crop Left: N/A")
        self.left_perc_label.grid(row=0, column=2, padx=2,pady=2, sticky="w")
        self.right_perc_label = ctk.CTkLabel(perc_frame, text="Crop Right: N/A")
        self.right_perc_label.grid(row=0, column=3, padx=2,pady=2, sticky="w")

        controls_frame = ctk.CTkFrame(main_frame)
        controls_frame.pack(fill="x", padx=5, pady=5)
        self.open_video_button = ctk.CTkButton(controls_frame, text="Open Video", command=self._open_video_file_dialog)
        self.open_video_button.pack(side="left", padx=5, pady=5)
        self.loaded_video_label = ctk.CTkLabel(controls_frame, text="No video loaded", anchor="w", wraplength=400)
        self.loaded_video_label.pack(side="left", padx=10, pady=5, fill="x", expand=True)
        self.save_cfg_button = ctk.CTkButton(controls_frame, text="Save to general.cfg & Close", command=self._save_config_and_close)
        self.save_cfg_button.pack(side="right", padx=5, pady=5)
        self.cancel_button = ctk.CTkButton(controls_frame, text="Cancel", command=self._on_close)
        self.cancel_button.pack(side="right", padx=5, pady=5)

    def _try_load_first_video(self):
        print("DEBUG: Attempting to auto-load first video...")
        folder_path_str = self.video_input_folder_var.get()
        if not folder_path_str:
            print("DEBUG: Video input folder path is not set. Cannot auto-load.")
            self.video_frame_widget.set_pil_image(None)
            return

        folder_p = Path(folder_path_str)
        resolved_folder_path = folder_p if folder_p.is_absolute() else (BASE_PATH / folder_p).resolve()
        print(f"DEBUG: Resolved video input folder: {resolved_folder_path}")

        if not resolved_folder_path.is_dir():
            print(f"DEBUG: Video input path is not a valid directory: {resolved_folder_path}")
            self.video_frame_widget.set_pil_image(None)
            self.loaded_video_label.configure(text=f"Error: Input path not a directory.")
            return

        first_video_file = None
        try:
            sorted_files = sorted(resolved_folder_path.glob('*.*'))
            for item in sorted_files:
                if item.is_file() and item.suffix.lower() in VIDEO_FILE_EXTENSIONS:
                    first_video_file = item
                    print(f"DEBUG: Found first video file: {first_video_file}")
                    break
        except Exception as e:
            print(f"ERROR: Error scanning directory {resolved_folder_path}: {e}")
            self._show_error(f"Error scanning video input directory:\n{resolved_folder_path}\n{e}")
            self.video_frame_widget.set_pil_image(None)
            return

        if first_video_file:
            print(f"DEBUG: Calling _load_video for {first_video_file}")
            self._load_video(str(first_video_file))
        else:
            print(f"DEBUG: No video files found in {resolved_folder_path}")
            self.video_frame_widget.set_pil_image(None)
            self.loaded_video_label.configure(text=f"No video files found in input folder.")

    def _load_video(self, file_path):
        print(f"DEBUG: Loading video: {file_path}")
        self.video_path = file_path
        info = self._get_video_info(self.video_path)
        if not info:
            self.video_path = None
            self.video_frame_widget.set_pil_image(None)
            self.time_slider.configure(state="disabled")
            self.loaded_video_label.configure(text="Error loading video info")
            self.video_width = 0
            self.video_height = 0
            self.video_fps = 0
            self.video_total_frames = 0
            self.video_duration_ms = 0
            return False

        self.video_width = info['width']
        self.video_height = info['height']
        self.video_fps = info['fps']
        self.video_total_frames = info['total_frames']
        self.video_duration_ms = info['duration_ms']
        print(f"DEBUG: Video Info: {self.video_width}x{self.video_height} @ {self.video_fps:.2f}fps, {self.video_duration_ms}ms, {self.video_total_frames} frames")

        if self.cap:
            self.cap.release()
        try:
            self.cap = cv2.VideoCapture(self.video_path)
            if not self.cap.isOpened():
                raise IOError(f"Could not open video with OpenCV: {self.video_path}")
        except Exception as e:
            self._show_error(f"OpenCV Error: {e}")
            self.cap = None
            self.video_path = None
            self.video_frame_widget.set_pil_image(None)
            self.time_slider.configure(state="disabled")
            self.loaded_video_label.configure(text="Error opening video (OpenCV)")
            self.video_width = 0
            self.video_height = 0
            return False

        self.video_frame_widget.set_video_properties(self.video_width, self.video_height)

        slider_max = 100
        if self.video_duration_ms > 0:
            slider_max = self.video_duration_ms
            self.time_slider.configure(to=slider_max, state="normal")
        elif self.video_total_frames > 0 and self.video_fps > 0:
             slider_max = max(1, self.video_total_frames - 1)
             self.time_slider.configure(to=slider_max, state="normal")
        else:
            self.time_slider.configure(to=100, state="disabled")
            print("WARN: Video has insufficient duration/frame information for slider.")

        self.time_slider.set(0)

        total_time_ms_display = self.video_duration_ms if self.video_duration_ms > 0 else 0
        self.total_time_label.configure(text=self._format_time(total_time_ms_display))
        self.loaded_video_label.configure(text=f"Loaded: {Path(self.video_path).name}")

        self._seek_to_time(0)
        self.display_percentage_labels(self.video_frame_widget.get_current_percentages_ini_style())
        print("DEBUG: Video loaded successfully.")
        return True

    def _get_video_info(self, filepath):
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            command = [ FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath ]
            result = subprocess.run(command, capture_output=True, text=True, check=True, startupinfo=startupinfo, encoding='utf-8', errors='replace')
            data = json.loads(result.stdout)

            video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream: self._show_error("No video stream found."); return None

            width = int(video_stream.get('width', 0)); height = int(video_stream.get('height', 0))
            if width <= 0 or height <= 0: self._show_error(f"Video stream has invalid dimensions: {width}x{height}."); return None

            duration_str = video_stream.get('duration', data.get('format', {}).get('duration', None))
            duration_ms = 0
            if duration_str is not None:
                 try:
                     duration_ms = int(float(duration_str) * 1000)
                 except (ValueError, TypeError):
                     print(f"WARN: Could not parse duration '{duration_str}'")
                     duration_ms = 0

            fps_str = video_stream.get('avg_frame_rate', "0/0")
            if fps_str in ("0/0", "0/1"): fps_str = video_stream.get('r_frame_rate', "0/0")

            num, den = 0, 1
            fps = 0.0
            if '/' in fps_str:
                 try:
                     num, den = map(int, fps_str.split('/'))
                     if den != 0: fps = num / den
                 except ValueError:
                     print(f"WARN: Could not parse FPS ratio '{fps_str}'")
            elif fps_str != "0":
                 try:
                     fps = float(fps_str)
                     num = fps
                     den = 1
                 except ValueError:
                      print(f"WARN: Could not parse FPS float '{fps_str}'")

            total_frames_str = video_stream.get('nb_frames', "0")
            total_frames = 0
            try:
                 parsed_frames = int(total_frames_str)
                 if parsed_frames > 0 : total_frames = parsed_frames
            except (ValueError, TypeError): pass

            if total_frames == 0 and duration_ms > 0 and fps > 0:
                total_frames = int((duration_ms / 1000.0) * fps)
                print(f"DEBUG: Estimated total frames from duration/fps: {total_frames}")

            if duration_ms <= 0 and total_frames > 0 and fps > 0:
                duration_ms = int((total_frames / fps) * 1000.0)
                print(f"DEBUG: Estimated duration from frames/fps: {duration_ms}ms")

            if duration_ms <= 0 and total_frames <= 0 :
                 print("WARN: Could not determine reliable duration or total frame count.")

            return {"width": width, "height": height, "fps": fps, "total_frames": total_frames, "duration_ms": duration_ms}

        except FileNotFoundError: self._show_error(f"{FFPROBE_PATH} not found. Please ensure it's installed and in your PATH."); return None
        except subprocess.CalledProcessError as e: self._show_error(f"ffprobe error: {e.stderr if e.stderr else 'Unknown error'}"); return None
        except json.JSONDecodeError as e: self._show_error(f"Error parsing ffprobe output: {e}"); return None
        except Exception as e: self._show_error(f"Error parsing video info: {e}\n{traceback.format_exc()}"); return None

    def _open_video_file_dialog(self):
        initial_dir = self.video_input_folder_var.get()
        resolved_initial_dir = str(BASE_PATH)
        if initial_dir:
            initial_p = Path(initial_dir)
            resolved_initial_dir_p = initial_p if initial_p.is_absolute() else (BASE_PATH / initial_p)
            if resolved_initial_dir_p.is_dir():
                resolved_initial_dir = str(resolved_initial_dir_p)
            else:
                resolved_initial_dir = str(resolved_initial_dir_p.parent) if resolved_initial_dir_p.parent.is_dir() else str(BASE_PATH)

        file_path = filedialog.askopenfilename(
            master=self,
            title="Open Video File",
            initialdir=resolved_initial_dir,
            filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.flv *.wmv"), ("All Files", "*.*")]
        )
        if not file_path: return
        self._load_video(file_path)

    def _format_time(self, ms):
        if ms < 0: ms = 0
        s, msecs = divmod(ms, 1000)
        mins, secs = divmod(s, 60)
        hrs, mins = divmod(mins, 60)
        if hrs > 0:
            return f"{int(hrs):d}:{int(mins):02d}:{int(secs):02d}.{int(msecs):03d}"
        else:
            return f"{int(mins):02d}:{int(secs):02d}.{int(msecs):03d}"

    def _get_slider_time_ms(self, slider_value):
        if self.video_duration_ms > 0:
            return min(int(slider_value), self.video_duration_ms)
        elif self.video_total_frames > 0 and self.video_fps > 0:
             frame_number = int(slider_value)
             frame_number = max(0, min(frame_number, self.video_total_frames - 1))
             return int((frame_number / self.video_fps) * 1000.0)
        else:
            return 0

    def slider_seek_handler(self, value_str):
        if not self.cap or not self.cap.isOpened(): return

        try:
             value = float(value_str)
        except ValueError:
             return

        time_ms = self._get_slider_time_ms(value)
        self.current_time_label.configure(text=self._format_time(time_ms))
        self._seek_to_time(time_ms)

    def _seek_to_time(self, time_ms):
        if not self.cap or not self.cap.isOpened():
             print("DEBUG: Seek attempted but video capture not ready.")
             return

        if self.video_duration_ms > 0:
            time_ms = max(0, min(time_ms, self.video_duration_ms))
        else:
             time_ms = max(0, time_ms)

        target_prop = cv2.CAP_PROP_POS_MSEC
        target_val = float(time_ms)

        try:
            if not self.cap.set(target_prop, target_val):
                 print(f"WARN: cap.set(cv2.CAP_PROP_POS_MSEC, {target_val}) returned False")

            ret, frame = self.cap.read()

            if ret:
                rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(rgb_image)
                self.video_frame_widget.set_pil_image(pil_image)
            else:
                print(f"WARN: Frame read failed after seeking to {time_ms} ms.")

            current_slider_val = self.time_slider.get()
            target_slider_val = 0

            if self.video_duration_ms > 0:
                 target_slider_val = time_ms
            elif self.video_total_frames > 0 and self.video_fps > 0:
                 target_slider_val = int((time_ms / 1000.0) * self.video_fps)
                 target_slider_val = max(0, min(target_slider_val, self.video_total_frames - 1))

            if abs(current_slider_val - target_slider_val) > 1:
                original_command = self.time_slider.cget("command")
                self.time_slider.configure(command=None)
                self.time_slider.set(target_slider_val)
                self.time_slider.configure(command=original_command)

            self.current_time_label.configure(text=self._format_time(time_ms))

        except Exception as e:
            print(f"ERROR: Exception during seek/read: {e}\n{traceback.format_exc()}")

    def update_percentage_labels_and_storage(self, percentages_ini_style):
        self.current_crop_percentages_ini_style = percentages_ini_style.copy()
        self.display_percentage_labels(percentages_ini_style)

    def display_percentage_labels(self, percentages_ini_style):
        fmt = ".6f"
        try:
            self.top_perc_label.configure(text=f"Crop Top: {float(percentages_ini_style.get('top_video_image_percent_end', 0.0)):{fmt}}")
            self.bottom_perc_label.configure(text=f"Crop Bottom: {float(percentages_ini_style.get('bottom_video_image_percent_end', 0.0)):{fmt}}")
            self.left_perc_label.configure(text=f"Crop Left: {float(percentages_ini_style.get('left_video_image_percent_end', 0.0)):{fmt}}")
            self.right_perc_label.configure(text=f"Crop Right: {float(percentages_ini_style.get('right_video_image_percent_end', 0.0)):{fmt}}")
        except (ValueError, TypeError) as e:
             print(f"ERROR: Could not format percentage labels: {e}, data: {percentages_ini_style}")
             self.top_perc_label.configure(text="INI top: Error")
             self.bottom_perc_label.configure(text="INI bottom: Error")
             self.left_perc_label.configure(text="INI left: Error")
             self.right_perc_label.configure(text="INI right: Error")


    def _save_config_and_close(self):
        general_cfg_path_str = self.general_cfg_path_var.get()
        if not general_cfg_path_str:
            self._show_error("Path to general.cfg is not set in the main application.")
            return

        general_cfg_p = Path(general_cfg_path_str)
        general_cfg_file = general_cfg_p if general_cfg_p.is_absolute() else (BASE_PATH / general_cfg_p).resolve()

        current_percentages_float = self.video_frame_widget.get_current_percentages_ini_style()

        vals_to_write_str = {}
        for key, float_val in current_percentages_float.items():
            s = f"{float(float_val):.7f}".rstrip('0')
            if s.endswith('.'): s = s[:-1]
            vals_to_write_str[key] = s if s else "0"

        output_lines = []
        managed_keys_found_in_file = {key: False for key in CROP_SETTING_KEYS_ORDER}
        other_lines = []

        if general_cfg_file.exists():
            try:
                with open(general_cfg_file, 'r', encoding='utf-8') as f:
                    for line_content in f:
                        original_line = line_content.rstrip('\n\r')
                        stripped_line = original_line.strip()

                        key_in_this_line = None
                        is_comment_or_empty = stripped_line.startswith('#') or not stripped_line
                        potential_key = ""

                        if not is_comment_or_empty:
                             eq_pos = stripped_line.find('=')
                             col_pos = stripped_line.find(':')
                             sep_pos = -1

                             if eq_pos != -1 and (col_pos == -1 or eq_pos < col_pos): sep_pos = eq_pos
                             elif col_pos != -1 and (eq_pos == -1 or col_pos < eq_pos): sep_pos = col_pos
                             elif eq_pos !=-1: sep_pos = eq_pos
                             elif col_pos != -1: sep_pos = col_pos

                             if sep_pos != -1:
                                 potential_key = stripped_line[:sep_pos].strip()
                                 if potential_key in vals_to_write_str:
                                     key_in_this_line = potential_key

                        if key_in_this_line:
                            output_lines.append(f"{key_in_this_line} = {vals_to_write_str[key_in_this_line]}")
                            managed_keys_found_in_file[key_in_this_line] = True
                        else:
                            other_lines.append(original_line)
            except Exception as e:
                self._show_error(f"Error reading {general_cfg_file.name} for update: {e}. Save aborted.")
                return
            final_output_lines = other_lines + output_lines
        else:
             final_output_lines = []

        keys_to_add = []
        for key_to_check in CROP_SETTING_KEYS_ORDER:
            if key_to_check in vals_to_write_str and not managed_keys_found_in_file[key_to_check]:
                keys_to_add.append(f"{key_to_check} = {vals_to_write_str[key_to_check]}")

        final_output_lines.extend(keys_to_add)

        try:
            general_cfg_file.parent.mkdir(parents=True, exist_ok=True)
            with open(general_cfg_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(final_output_lines) + '\n')

            if self.main_app_refresh_callback:
                self.main_app_refresh_callback()

            messagebox.showinfo("Save Successful", f"Crop settings saved to\n{general_cfg_file}", parent=self)
            self._on_close()

        except Exception as e:
            self._show_error(f"Error writing configuration to {general_cfg_file.name}: {e}")

    def _on_close(self):
        if self.cap: self.cap.release()
        self.grab_release()
        self.destroy()

    def _show_error(self, message):
        messagebox.showerror("Crop Editor Error", message, parent=self)


# --- DirectoryMonitorHandler ---
class DirectoryMonitorHandler(FileSystemEventHandler):
    def __init__(self, output_queue):
        super().__init__()
        self.output_queue = output_queue

    def on_created(self, event):
        if not event.is_directory:
            src_path_str = str(event.src_path)
            if os.path.basename(os.path.dirname(src_path_str)) == "RGBImages":
                self.output_queue.put(f"Crop Text Images [RGBImages]: {os.path.basename(event.src_path)} .Done")
            elif os.path.basename(os.path.dirname(src_path_str)) == "TXTImages":
                self.output_queue.put(f"Cleared Text Images [TXTImages]: {os.path.basename(event.src_path)} .Done")

# --- VideoSubFinderGUI Class (Main Application) ---
class VideoSubFinderGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("900x850")
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.config_parser = configparser.ConfigParser(allow_no_value=True)
        self.abs_script_path = BASE_PATH
        self.current_run_output_dir = None

        self.processing_thread = None
        self.monitoring_thread = None # For the observer's own thread management
        self.stop_event = threading.Event()
        self.current_vsf_process = None
        self.observer = None
        self.crop_editor_window = None
        self.edit_crop_visual_button = None # Will hold the moved button

        self.log_queue = queue.Queue()

        self.main_frame = None
        self.paths_frame = None
        self.settings_frame = None
        self.controls_frame = None
        self.log_frame = None

        self.open_folder_buttons = []

        self._init_ui()
        self.load_settings()
        self.after(100, self.process_log_queue)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.log_message("|===========================================================================================|")
        self.log_message(f" {VERSION_INFO}")
        self.log_message("|===========================================================================================|")
        self.log_message("1 # Browse program path 'VideoSubFinderWXW.exe' #")
        self.log_message("2 # ADD Multiple videos to folder 'Paste_Multi_Videos_Here' (or Browse configured path folder videos)#")
        self.log_message("3 # General Settings Get a file from (VSF) and Browse path 'general.cfg' #")
        self.log_message("4 # Default folder 'Output_Videos_Images'(or configured path)#")
        self.log_message("5 # Change VideoSubFinder settings #")
        self.log_message("6 # Crop settings (Top/Bottom/Left/Right %) are loaded from/saved to general.cfg file. #")
        self.log_message("7 #use 'Edit Crop Visually' button to adjust general.cfg crop settings.")
        self.log_message("8 # Start Processing and wait until all Videos processed #")
        self.log_message("|===========================================================================================|")

    def _init_ui(self):
        self.main_frame = ctk.CTkFrame(self)
        self.main_frame.pack(padx=10, pady=10, fill="both", expand=True)

        # --- Paths Frame ---
        self.paths_frame = ctk.CTkFrame(self.main_frame)
        self.paths_frame.pack(pady=5, padx=10, fill="x")
        self.paths_frame.grid_columnconfigure(1, weight=1)
        self.paths_frame.grid_columnconfigure((2,3), weight=0)
        ctk.CTkLabel(self.paths_frame, text="Paths Configuration", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=4, pady=(0,5), sticky="ew")

        self.paths_vars = {}
        path_configs = [
            ("videosubfinder_path", "VideoSubFinder Executable:"),
            ("Videos_path", "Videos Input Folder:"),
            ("general_settings", "General Settings (.cfg):"),
            ("output_path", "Images Output Folder:"),
        ]
        for i, (key, text) in enumerate(path_configs):
            row_idx = i + 1
            ctk.CTkLabel(self.paths_frame, text=text).grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
            self.paths_vars[key] = ctk.StringVar()
            entry = ctk.CTkEntry(self.paths_frame, textvariable=self.paths_vars[key])
            entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")

            browse_command = None
            button_text = ""
            if key == "videosubfinder_path":
                button_text = "Browse .exe"
                browse_command = lambda k=key: self._browse_file(self.paths_vars[k], file_types=[("Executable", "*.exe"), ("All files", "*.*")])
            elif key == "general_settings":
                button_text = "Browse .cfg" # Only browse for general_settings, edit button is moved
                browse_command = lambda k=key: self._browse_file(self.paths_vars[k], file_types=[("Config File", "*.cfg"), ("All files", "*.*")])
            else: # Videos_path, output_path
                button_text = "Browse Folder"
                browse_command = lambda k=key: self._browse_folder(self.paths_vars[k])

            ctk.CTkButton(self.paths_frame, text=button_text, width=110, command=browse_command).grid(row=row_idx, column=2, padx=(5, 2), pady=5)

            action_button = None
            if key in ["Videos_path", "output_path"]:
                open_folder_command = lambda k_var=self.paths_vars[key]: self._open_folder_in_explorer(k_var)
                action_button = ctk.CTkButton(self.paths_frame, text="Open Folder", width=110, command=open_folder_command)
                self.open_folder_buttons.append(action_button)
            # The "Edit Crop Visually" button is NO LONGER created here for general_settings
            # It will be created in the settings_frame

            if action_button: # This will only be true for "Open Folder" buttons now
                 action_button.grid(row=row_idx, column=3, padx=(2, 5), pady=5)

        # --- Settings Frame ---
        self.settings_frame = ctk.CTkFrame(self.main_frame)
        self.settings_frame.pack(pady=5, padx=10, fill="x")
        self.settings_frame.grid_columnconfigure((0,1,2,3), weight=1)
        ctk.CTkLabel(self.settings_frame, text="VideoSubFinder Settings (from Settings.ini)", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=4, pady=(0,5),sticky="ew")

        self.settings_vars = {}

        ctk.CTkLabel(self.settings_frame, text="Video Open Mode:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.settings_vars["mode_open_video"] = ctk.StringVar(value="-ovffmpeg")
        ctk.CTkComboBox(self.settings_frame, variable=self.settings_vars["mode_open_video"], values=["-ovffmpeg", "-ovocv"], width=150).grid(row=1, column=1, padx=5, pady=5, sticky="w")

        self.settings_vars["create_cleared_text_images"] = ctk.BooleanVar()
        ctk.CTkCheckBox(self.settings_frame, text="Create Cleared Text Images", variable=self.settings_vars["create_cleared_text_images"]).grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky="w")
        self.settings_vars["use_cuda"] = ctk.BooleanVar()
        ctk.CTkCheckBox(self.settings_frame, text="Use CUDA (NVIDIA Support)", variable=self.settings_vars["use_cuda"]).grid(row=2, column=2, columnspan=2, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(self.settings_frame, text="Start Time (HH:MM:SS.SSS):").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.settings_vars["start_time"] = ctk.StringVar()
        ctk.CTkEntry(self.settings_frame, textvariable=self.settings_vars["start_time"], width=120).grid(row=3, column=1, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(self.settings_frame, text="End Time (HH:MM:SS.SSS):").grid(row=3, column=2, padx=5, pady=5, sticky="w")
        self.settings_vars["end_time"] = ctk.StringVar()
        ctk.CTkEntry(self.settings_frame, textvariable=self.settings_vars["end_time"], width=120).grid(row=3, column=3, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(self.settings_frame, text="Threads for RGB Images:").grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.settings_vars["number_threads_rgbimages"] = ctk.StringVar()
        ctk.CTkEntry(self.settings_frame, textvariable=self.settings_vars["number_threads_rgbimages"], width=50).grid(row=4, column=1, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(self.settings_frame, text="Threads for TXT Images:").grid(row=4, column=2, padx=5, pady=5, sticky="w")
        self.settings_vars["number_threads_txtimages"] = ctk.StringVar()
        ctk.CTkEntry(self.settings_frame, textvariable=self.settings_vars["number_threads_txtimages"], width=50).grid(row=4, column=3, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(self.settings_frame, text="Image Crop Area (Loaded from/Saved to general.cfg)", font=ctk.CTkFont(weight="bold")).grid(row=5, column=0, columnspan=4, pady=(10,5), sticky="ew")

        self.settings_vars["top_video_image_percent_end"] = ctk.StringVar()
        self.settings_vars["left_video_image_percent_end"] = ctk.StringVar()
        self.settings_vars["bottom_video_image_percent_end"] = ctk.StringVar()
        self.settings_vars["right_video_image_percent_end"] = ctk.StringVar()

        ctk.CTkLabel(self.settings_frame, text="Crop Top (%):").grid(row=6, column=0, padx=5, pady=5, sticky="w")
        ctk.CTkEntry(self.settings_frame, state='readonly', textvariable=self.settings_vars["top_video_image_percent_end"], width=100).grid(row=6, column=1, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(self.settings_frame, text="Crop Left (%):").grid(row=6, column=2, padx=5, pady=5, sticky="w")
        ctk.CTkEntry(self.settings_frame, state='readonly', textvariable=self.settings_vars["left_video_image_percent_end"], width=100).grid(row=6, column=3, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(self.settings_frame, text="Crop Bottom (%):").grid(row=7, column=0, padx=5, pady=5, sticky="w")
        ctk.CTkEntry(self.settings_frame, state='readonly', textvariable=self.settings_vars["bottom_video_image_percent_end"], width=100).grid(row=7, column=1, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(self.settings_frame, text="Crop Right (%):").grid(row=7, column=2, padx=5, pady=5, sticky="w")
        ctk.CTkEntry(self.settings_frame, state='readonly', textvariable=self.settings_vars["right_video_image_percent_end"], width=100).grid(row=7, column=3, padx=5, pady=5, sticky="w")

        # --- MODIFIED: "Edit Crop Visually" button moved here ---
        self.edit_crop_visual_button = ctk.CTkButton(self.settings_frame, text="Edit Crop Visually", command=self.open_crop_editor)
        self.edit_crop_visual_button.grid(row=8, column=0, columnspan=4, padx=5, pady=(10,5), sticky="ew") # Full width button


        # --- Controls Frame ---
        self.controls_frame = ctk.CTkFrame(self.main_frame)
        self.controls_frame.pack(pady=10, padx=10, fill="x")
        self.start_button = ctk.CTkButton(self.controls_frame, text="Start Processing", command=self.start_processing)
        self.start_button.pack(side="left", padx=5, pady=5, expand=True)
        self.stop_button = ctk.CTkButton(self.controls_frame, text="Stop Processing", command=self.stop_processing, state="disabled")
        self.stop_button.pack(side="left", padx=5, pady=5, expand=True)
        self.save_button = ctk.CTkButton(self.controls_frame, text="Save Settings", command=self.save_settings)
        self.save_button.pack(side="left", padx=5, pady=5, expand=True)

        # --- Log Frame ---
        self.log_frame = ctk.CTkFrame(self.main_frame)
        self.log_frame.pack(pady=5, padx=10, fill="both", expand=True)
        ctk.CTkLabel(self.log_frame, text="Output Log", font=ctk.CTkFont(weight="bold")).pack(pady=(0,5))
        self.log_text = ctk.CTkTextbox(self.log_frame, wrap="word", state="disabled", height=150)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def open_crop_editor(self):
        if self.crop_editor_window is None or not self.crop_editor_window.winfo_exists():
            general_cfg_path_str = self.paths_vars["general_settings"].get()
            if not general_cfg_path_str:
                messagebox.showwarning("Visual Crop Editor", "Path to 'General Settings (.cfg)' is not set. Please set it first.", parent=self)
                return

            initial_crop_settings_for_editor = {}
            try:
                for key in DEFAULT_CROP_SETTINGS.keys():
                    val_str = self.settings_vars[key].get()
                    initial_crop_settings_for_editor[key] = float(val_str if val_str else DEFAULT_CROP_SETTINGS[key])
            except (ValueError, KeyError) as e:
                messagebox.showerror("Visual Crop Editor", f"Current crop percentage values in the UI are invalid or missing key '{e}'. Please correct them or check general.cfg.", parent=self)
                self.log_queue.put(f"Error preparing crop settings for editor: {e}. Using defaults.")
                initial_crop_settings_for_editor = {k: float(v) for k, v in DEFAULT_CROP_SETTINGS.items()}

            self.crop_editor_window = CropRegionEditorWindow(
                master=self,
                general_cfg_path_var=self.paths_vars["general_settings"],
                video_input_folder_var=self.paths_vars["Videos_path"],
                main_app_refresh_callback=self._load_general_cfg_settings,
                initial_crop_settings=initial_crop_settings_for_editor
            )
            self.crop_editor_window.focus_set()
        else:
            self.crop_editor_window.focus_set()

    def _browse_file(self, string_var, file_types=None):
        current_path = string_var.get()
        initial_dir = str(BASE_PATH)
        if current_path:
            p = Path(current_path)
            if p.is_file(): initial_dir = str(p.parent)
            elif p.is_dir(): initial_dir = str(p)
            elif p.parent.exists(): initial_dir = str(p.parent)

        file_path = filedialog.askopenfilename(initialdir=initial_dir, filetypes=file_types if file_types else [])
        if file_path:
            string_var.set(file_path)
            if "general_settings" in self.paths_vars and string_var == self.paths_vars["general_settings"]:
                self.log_queue.put(f"General settings file selected via browse: {file_path}. Reloading crop settings from it.")
                self._load_general_cfg_settings()

    def _browse_folder(self, string_var):
        current_path = string_var.get()
        initial_dir = str(BASE_PATH)
        if current_path:
             p = Path(current_path)
             if p.is_dir(): initial_dir = str(p)
             elif p.parent.is_dir(): initial_dir = str(p.parent)

        folder_path = filedialog.askdirectory(initialdir=initial_dir)
        if folder_path:
            string_var.set(folder_path)

    def _open_folder_in_explorer(self, string_var):
        folder_path_str = string_var.get()
        resolved_folder_path = None
        path_key = None
        for k, v_str_var in self.paths_vars.items():
            if v_str_var == string_var: path_key = k; break

        if not folder_path_str:
            if path_key == "output_path":
                default_output = DEFAULT_SETTINGS["Path"].get("output_path", DEFAULT_OUTPUT_RELPATH)
                resolved_folder_path = (self.abs_script_path / default_output).resolve()
                self.log_queue.put(f"Output path is empty, attempting to open default: {resolved_folder_path}")
            elif path_key == "Videos_path":
                default_videos = DEFAULT_SETTINGS["Path"].get("Videos_path", "Paste_Multi_Videos_Here")
                resolved_folder_path = (self.abs_script_path / default_videos).resolve()
                self.log_queue.put(f"Videos path is empty, attempting to open default: {resolved_folder_path}")
            else:
                 messagebox.showwarning("Open Folder", "Path is not set.", parent=self)
                 self.log_queue.put(f"Attempted to open folder for '{path_key}', but path is not set.")
                 return
        else:
            folder_p = Path(folder_path_str)
            resolved_folder_path = folder_p if folder_p.is_absolute() else (self.abs_script_path / folder_p).resolve()

        if not resolved_folder_path:
             messagebox.showerror("Open Folder", "Could not determine folder path.", parent=self)
             return

        if not resolved_folder_path.exists():
            if messagebox.askyesno("Open Folder", f"Path does not exist:\n{resolved_folder_path}\n\nDo you want to create it?", parent=self):
                 try:
                     resolved_folder_path.mkdir(parents=True, exist_ok=True)
                     self.log_queue.put(f"Created directory: {resolved_folder_path}")
                 except Exception as e:
                     messagebox.showerror("Open Folder", f"Failed to create directory:\n{e}", parent=self)
                     self.log_queue.put(f"Error creating directory {resolved_folder_path}: {e}")
                     return
            else:
                 self.log_queue.put(f"Attempted to open non-existent folder, user chose not to create: {resolved_folder_path}")
                 return

        if not resolved_folder_path.is_dir():
            messagebox.showerror("Open Folder", f"Path exists but is not a directory:\n{resolved_folder_path}", parent=self)
            self.log_queue.put(f"Attempted to open path that is not a directory: {resolved_folder_path}")
            return

        try:
            if os.name == 'nt': os.startfile(str(resolved_folder_path))
            elif sys.platform == 'darwin': subprocess.run(['open', str(resolved_folder_path)], check=True)
            else: subprocess.run(['xdg-open', str(resolved_folder_path)], check=True)
            self.log_queue.put(f"Opened folder: {resolved_folder_path}")
        except FileNotFoundError: messagebox.showerror("Open Folder", "Could not find a program to open the folder.", parent=self); self.log_queue.put(f"Error opening folder {resolved_folder_path}: File opener not found.")
        except Exception as e: messagebox.showerror("Open Folder", f"Failed to open folder: {e}", parent=self); self.log_queue.put(f"Error opening folder {resolved_folder_path}: {e}")

    def log_message(self, message):
        if self.log_text.winfo_exists():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", str(message) + "\n")
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
            self.update_idletasks()

    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_message(message)
        except queue.Empty:
            pass
        finally:
            if self.winfo_exists():
                self.after(100, self.process_log_queue)

    def _parse_general_cfg_line_for_load(self, line_content):
        line = line_content.strip()
        if not line or line.startswith('#'): return None, None

        key, value = None, None
        eq_pos = line.find('=')
        col_pos = line.find(':')
        sep_pos = -1

        if eq_pos != -1 and (col_pos == -1 or eq_pos < col_pos):
            sep_pos = eq_pos
        elif col_pos != -1 and (eq_pos == -1 or col_pos < eq_pos):
            sep_pos = col_pos
        elif eq_pos != -1:
             sep_pos = eq_pos
        elif col_pos != -1:
             sep_pos = col_pos

        if sep_pos != -1:
            parsed_key = line[:sep_pos].strip()
            if parsed_key in DEFAULT_CROP_SETTINGS:
                key = parsed_key
                value_part = line[sep_pos+1:].strip()
                comment_start = value_part.find('#')
                value = value_part[:comment_start].strip() if comment_start != -1 else value_part
                try: float(value)
                except ValueError:
                     self.log_queue.put(f"Warning: Invalid numeric value '{value}' for key '{key}' in general.cfg. Using default.")
                     value = DEFAULT_CROP_SETTINGS[key]
        return key, value

    def _parse_general_cfg_line_for_save(self, line_content):
        line = line_content.strip()
        original_line_text = line_content.rstrip('\n\r')
        if not line or line.startswith('#'): return None, original_line_text

        key = None
        eq_pos = line.find('=')
        col_pos = line.find(':')
        sep_pos = -1
        if eq_pos != -1 and (col_pos == -1 or eq_pos < col_pos): sep_pos = eq_pos
        elif col_pos != -1 and (eq_pos == -1 or col_pos < eq_pos): sep_pos = col_pos
        elif eq_pos != -1: sep_pos = eq_pos
        elif col_pos != -1: sep_pos = col_pos

        if sep_pos != -1:
            potential_key = line[:sep_pos].strip()
            if potential_key in DEFAULT_CROP_SETTINGS:
                key = potential_key
        return key, original_line_text

    def _load_general_cfg_settings(self):
        general_settings_var = self.paths_vars.get("general_settings")
        general_cfg_path_str = general_settings_var.get() if general_settings_var else ""
        default_crop_str_values = {k: str(v) for k, v in DEFAULT_CROP_SETTINGS.items()}
        loaded_crop_settings = {}

        for key_cfg, default_val_str in default_crop_str_values.items():
            loaded_crop_settings[key_cfg] = default_val_str

        general_cfg_file = None
        if general_cfg_path_str:
            general_cfg_p = Path(general_cfg_path_str)
            general_cfg_file = general_cfg_p if general_cfg_p.is_absolute() else (self.abs_script_path / general_cfg_p).resolve()

        if general_cfg_file and general_cfg_file.exists():

            try:
                with open(general_cfg_file, 'r', encoding='utf-8') as f:
                    for line_content in f:
                        key, value_str = self._parse_general_cfg_line_for_load(line_content)
                        if key and value_str is not None:
                             try:
                                 float(value_str)
                                 loaded_crop_settings[key] = value_str
                             except ValueError:
                                 self.log_queue.put(f"Warning: Invalid non-numeric value '{value_str}' for key '{key}' in {general_cfg_file.name}. Using default '{default_crop_str_values[key]}'.")
                                 loaded_crop_settings[key] = default_crop_str_values[key]
            except Exception as e:
                self.log_queue.put(f"Error reading {general_cfg_file.name} for crop settings: {e}. Using default values.")
                loaded_crop_settings = default_crop_str_values.copy()
        elif general_cfg_path_str:
             self.log_queue.put(f"Warning: Specified general.cfg '{general_cfg_file}' not found. Using default crop settings for UI.")
        else:
            self.log_queue.put("Path to general.cfg is not set. Using default crop settings for UI.")

        try:
             for key_cfg, loaded_val_str in loaded_crop_settings.items():
                 if key_cfg in self.settings_vars:
                     self.settings_vars[key_cfg].set(loaded_val_str)
             self.log_queue.put("Main GUI crop setting display refreshed.")
        except Exception as e:
             self.log_queue.put(f"Error updating UI with loaded crop settings: {e}")

    def _save_general_cfg_settings(self):
        general_settings_var = self.paths_vars.get("general_settings")
        general_cfg_path_str = general_settings_var.get() if general_settings_var else ""

        if not general_cfg_path_str:
            self.log_queue.put("Path to general.cfg is not set. Crop settings from main GUI were not saved to it.")
            return False

        general_cfg_p = Path(general_cfg_path_str)
        general_cfg_file = general_cfg_p if general_cfg_p.is_absolute() else (self.abs_script_path / general_cfg_p).resolve()

        try:
            general_cfg_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log_queue.put(f"Error creating directory for {general_cfg_file.name}: {e}")
            messagebox.showerror("Save Error", f"Could not create directory for {general_cfg_file.name}:\n{e}", parent=self)
            return False

        current_gui_crop_values_str = {}
        try:
             for key in CROP_SETTING_KEYS_ORDER:
                 if key in self.settings_vars:
                     val_str = self.settings_vars[key].get()
                     float(val_str if val_str else "0")
                     current_gui_crop_values_str[key] = val_str if val_str else "0"
                 else:
                     self.log_queue.put(f"Warning: Crop setting key '{key}' not found in UI variables during save.")
                     current_gui_crop_values_str[key] = DEFAULT_CROP_SETTINGS[key]
        except ValueError as e:
             messagebox.showerror("Save Error", f"Invalid numeric value found in main GUI crop settings ({e}). Cannot save general.cfg.", parent=self)
             self.log_queue.put(f"Save Error: Invalid numeric value in main GUI crop setting StringVars: {e}")
             return False

        output_lines = []
        managed_keys_found_in_file = {key: False for key in CROP_SETTING_KEYS_ORDER}
        other_lines = []

        if general_cfg_file.exists():
            try:
                with open(general_cfg_file, 'r', encoding='utf-8') as f:
                    for line_content in f:
                        parsed_key, original_line = self._parse_general_cfg_line_for_save(line_content)
                        if parsed_key and parsed_key in current_gui_crop_values_str:
                            output_lines.append(f"{parsed_key} = {current_gui_crop_values_str[parsed_key]}")
                            managed_keys_found_in_file[parsed_key] = True
                        else:
                            other_lines.append(original_line)
                final_output_lines = other_lines + output_lines
            except Exception as e:
                self.log_queue.put(f"Error reading {general_cfg_file.name} during save: {e}")
                messagebox.showerror("Save Error", f"Could not read {general_cfg_file.name} for update:\n{e}", parent=self)
                return False
        else:
            final_output_lines = []

        keys_to_add = []
        for key_to_add in CROP_SETTING_KEYS_ORDER:
            if key_to_add in current_gui_crop_values_str and not managed_keys_found_in_file[key_to_add]:
                keys_to_add.append(f"{key_to_add} = {current_gui_crop_values_str[key_to_add]}")
        final_output_lines.extend(keys_to_add)

        try:
            with open(general_cfg_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(final_output_lines) + '\n')
            self.log_queue.put(f"Crop settings from main GUI saved to {general_cfg_file.name}")
            return True
        except Exception as e:
            self.log_queue.put(f"Error writing to {general_cfg_file.name}: {e}")
            messagebox.showerror("Save Error", f"Could not write to {general_cfg_file.name}:\n{e}", parent=self)
            return False

    def load_settings(self):
        settings_ini_path = self.abs_script_path / SETTINGS_FILE
        if not settings_ini_path.exists():
            self.log_message(f"Warning: '{SETTINGS_FILE}' not found. Creating with default values.")
            self._create_default_settings_file(settings_ini_path)
        try:
             self.config_parser.read(str(settings_ini_path), encoding='utf-8')
        except configparser.Error as e:
             self.log_message(f"Error reading {SETTINGS_FILE}: {e}. Using default settings.")
             self.config_parser = configparser.ConfigParser(allow_no_value=True)
             self._create_default_settings_file(settings_ini_path)
             self.config_parser.read(str(settings_ini_path), encoding='utf-8')

        for key, var in self.paths_vars.items():
            fallback = DEFAULT_SETTINGS["Path"].get(key, "")
            var.set(self.config_parser.get("Path", key, fallback=fallback))

        settings_section = "Settings"
        settings_defaults = DEFAULT_SETTINGS[settings_section]
        def get_setting(key, fallback_default):
            return self.config_parser.get(settings_section, key, fallback=fallback_default)

        self.settings_vars["mode_open_video"].set(get_setting("mode_open_video", settings_defaults["mode_open_video"]))
        ccti_val = get_setting("create_cleared_text_images", settings_defaults["create_cleared_text_images"])
        self.settings_vars["create_cleared_text_images"].set(bool(ccti_val and ccti_val.strip() == "-ccti"))
        cuda_val = get_setting("use_cuda", settings_defaults["use_cuda"])
        self.settings_vars["use_cuda"].set(bool(cuda_val and cuda_val.strip() == "-uc"))
        self.settings_vars["start_time"].set(get_setting("start_time", settings_defaults["start_time"]))
        self.settings_vars["end_time"].set(get_setting("end_time", settings_defaults["end_time"]))
        self.settings_vars["number_threads_rgbimages"].set(get_setting("number_threads_rgbimages", settings_defaults["number_threads_rgbimages"]))
        self.settings_vars["number_threads_txtimages"].set(get_setting("number_threads_txtimages", settings_defaults["number_threads_txtimages"]))
        self._load_general_cfg_settings()

    def _create_default_settings_file(self, path):
        temp_config = configparser.ConfigParser(allow_no_value=True)
        if "general_settings" not in DEFAULT_SETTINGS["Path"]: DEFAULT_SETTINGS["Path"]["general_settings"] = "general.cfg"

        for section, options in DEFAULT_SETTINGS.items():
            if not temp_config.has_section(section): temp_config.add_section(section)
            for key, value in options.items():
                temp_config.set(section, key, str(value))
        try:
             with open(path, 'w', encoding='utf-8') as configfile:
                 temp_config.write(configfile)
        except Exception as e:
             self.log_message(f"Error creating default settings file {path.name}: {e}")
             messagebox.showerror("Initialization Error", f"Could not create default settings file:\n{path}\n\nError: {e}")

    def save_settings(self):
        settings_ini_path = self.abs_script_path / SETTINGS_FILE
        ini_saved_ok = False
        try:
            if not self.config_parser.has_section("Path"): self.config_parser.add_section("Path")
            if not self.config_parser.has_section("Settings"): self.config_parser.add_section("Settings")

            for key, var in self.paths_vars.items():
                self.config_parser.set("Path", key, var.get())

            self.config_parser.set("Settings", "mode_open_video", self.settings_vars["mode_open_video"].get())
            self.config_parser.set("Settings", "create_cleared_text_images", "-ccti" if self.settings_vars["create_cleared_text_images"].get() else "")
            self.config_parser.set("Settings", "use_cuda", "-uc" if self.settings_vars["use_cuda"].get() else "")
            self.config_parser.set("Settings", "start_time", self.settings_vars["start_time"].get())
            self.config_parser.set("Settings", "end_time", self.settings_vars["end_time"].get())
            self.config_parser.set("Settings", "number_threads_rgbimages", self.settings_vars["number_threads_rgbimages"].get())
            self.config_parser.set("Settings", "number_threads_txtimages", self.settings_vars["number_threads_txtimages"].get())

            if self.config_parser.has_section("OCR"): self.config_parser.remove_section("OCR")

            with open(settings_ini_path, 'w', encoding='utf-8') as configfile:
                self.config_parser.write(configfile)
            self.log_message(f"Settings.ini saved to '{settings_ini_path.name}'.")
            ini_saved_ok = True
        except Exception as e:
            self.log_message(f"Error saving Settings.ini: {e}")
            messagebox.showerror("Save Error", f"Could not save Settings.ini:\n{e}", parent=self)

        general_cfg_saved_ok = self._save_general_cfg_settings()
        general_cfg_path_str = self.paths_vars.get("general_settings", ctk.StringVar()).get()

        if ini_saved_ok and (general_cfg_saved_ok or not general_cfg_path_str):
            msg = "Settings.ini saved successfully."
            if general_cfg_path_str and general_cfg_saved_ok:
                 msg = "All settings (Settings.ini and general.cfg) saved successfully!"
                 self._load_general_cfg_settings() # Reload UI display after successful save
            elif general_cfg_path_str and not general_cfg_saved_ok:
                 msg = "Settings.ini saved. Failed to save general.cfg (see log/previous error)."
            elif not general_cfg_path_str:
                 msg += "\nPath to general.cfg is not set; crop settings were not saved to it."
            messagebox.showinfo("Save Settings", msg, parent=self)
        elif not ini_saved_ok and general_cfg_saved_ok:
             messagebox.showwarning("Save Settings", "Failed to save Settings.ini.\nGeneral.cfg was saved successfully.", parent=self)
             self._load_general_cfg_settings() # Reload UI display even if ini failed but cfg saved

    def _set_controls_state(self, processing: bool):
        state = "disabled" if processing else "normal"
        readonly_state = "disabled" if processing else "readonly"

        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if processing else "disabled")
        self.save_button.configure(state=state)

        if self.paths_frame:
            for widget in self.paths_frame.winfo_children():
                widget_type = widget.winfo_class()
                if widget_type in ('CTkEntry', 'CTkButton'):
                    widget.configure(state=state)

        if self.settings_frame:
            for widget in self.settings_frame.winfo_children():
                widget_class = widget.winfo_class()
                if widget_class == 'CTkLabel':
                    continue
                if widget == self.edit_crop_visual_button:
                    widget.configure(state=state)
                    continue

                is_crop_related_entry = False
                try:
                    # Check if it's an entry with a textvariable linked to a crop setting
                    if hasattr(widget, 'cget') and widget_class == 'CTkEntry':
                        tv_name = widget.cget('textvariable')
                        if tv_name:
                            for setting_key, string_var_obj in self.settings_vars.items():
                                if str(string_var_obj) == tv_name:
                                    if setting_key in DEFAULT_CROP_SETTINGS:
                                        is_crop_related_entry = True
                                    break
                except Exception as e:
                    # Silently ignore errors (e.g., widget destroyed)
                    print(f"Debug: Error checking widget state: {e} on {widget}") # Optional debug print
                    pass

                # Apply the correct state
                if is_crop_related_entry:
                    widget.configure(state=readonly_state)
                elif widget_class in ('CTkEntry', 'CTkComboBox', 'CTkCheckBox'):
                    widget.configure(state=state)

    def start_processing(self):
        self.log_message("\n" + "="*60)
        self.log_message("--- Starting Video Processing ---")
        self.log_message("="*60)

        vsf_path_str = self.paths_vars["videosubfinder_path"].get()
        vsf_exe_p = Path(vsf_path_str)
        vsf_exe_abs = vsf_exe_p if vsf_exe_p.is_absolute() else (self.abs_script_path / vsf_exe_p).resolve()
        if not vsf_exe_abs.is_file():
            messagebox.showerror("Error", f"VideoSubFinder Executable not found or is not a file:\n{vsf_exe_abs}", parent=self)
            self.log_message(f"Error: VideoSubFinder Executable path invalid: {vsf_exe_abs}")
            return

        videos_input_path_str = self.paths_vars["Videos_path"].get()
        videos_input_p = Path(videos_input_path_str)
        videos_input_dir = videos_input_p if videos_input_p.is_absolute() else (self.abs_script_path / videos_input_p).resolve()
        if not videos_input_dir.is_dir():
            messagebox.showerror("Error", f"Videos input folder does not exist or is not a directory:\n{videos_input_dir}", parent=self)
            self.log_message(f"Error: Videos input folder invalid: {videos_input_dir}")
            return

        general_settings_path_str = self.paths_vars["general_settings"].get()
        resolved_gs_p = None
        if general_settings_path_str:
            gs_p = Path(general_settings_path_str)
            resolved_gs_p = gs_p if gs_p.is_absolute() else (self.abs_script_path / gs_p).resolve()
            if not resolved_gs_p.exists():
                self.log_message(f"Warning: General settings file specified but does not exist:\n{resolved_gs_p}")
                self.log_message("VSF will use internal defaults or settings from Settings.ini if applicable.")
            elif not resolved_gs_p.is_file():
                 messagebox.showerror("Error", f"The specified general settings path is not a file:\n{resolved_gs_p}", parent=self)
                 self.log_message(f"Error: General settings path is not a file: {resolved_gs_p}")
                 return

        # Check for video files BEFORE starting thread/disabling controls
        all_video_files = []
        if videos_input_dir.is_dir(): # Ensure directory is valid before globbing
            for ext in VIDEO_FILE_EXTENSIONS:
                all_video_files.extend(list(videos_input_dir.glob(f'*{ext}')))
                all_video_files.extend(list(videos_input_dir.glob(f'*{ext.upper()}')))
            all_video_files = sorted(list(set(all_video_files)))

        if not all_video_files:
            self.log_message(f"No video files ({', '.join(VIDEO_FILE_EXTENSIONS)}) found in the input directory: {videos_input_dir}")
            messagebox.showinfo("No Videos Found",
                                f"No video files ({', '.join(VIDEO_FILE_EXTENSIONS)}) found in the input directory:\n{videos_input_dir}\n\nProcessing cannot start.",
                                parent=self)
            self.log_message("--- Video Processing Aborted (No Videos) ---")
            return # Exit before disabling controls or starting thread
        self.log_message(f"Found {len(all_video_files)} video files to process in {videos_input_dir}.")

        custom_output_path_str = self.paths_vars["output_path"].get()
        output_rel_or_abs = custom_output_path_str if custom_output_path_str else DEFAULT_OUTPUT_RELPATH
        output_p = Path(output_rel_or_abs)
        self.current_run_output_dir = output_p if output_p.is_absolute() else (self.abs_script_path / output_p).resolve()

        try:
            self.current_run_output_dir.mkdir(parents=True, exist_ok=True)

        except Exception as e:
            messagebox.showerror("Error", f"Could not create output directory:\n{self.current_run_output_dir}\nError: {e}", parent=self)
            self.log_message(f"Error: Could not create output directory '{self.current_run_output_dir}': {e}")
            return

        self.stop_event.clear()
        self._set_controls_state(processing=True)
        # Pass the found video files to the processing target
        self.processing_thread = threading.Thread(target=self._processing_loop_target,
                                                  args=(str(self.current_run_output_dir), all_video_files),
                                                  daemon=True)
        self.processing_thread.start()
        self.start_monitoring(str(self.current_run_output_dir))


    def _processing_loop_target(self, current_output_dir_str, video_files_to_process):
        current_output_dir = Path(current_output_dir_str)
        all_video_files = video_files_to_process # Use the passed list

        try:
            vsf_exe_p = Path(self.paths_vars["videosubfinder_path"].get())
            vsf_exe_path = str(vsf_exe_p if vsf_exe_p.is_absolute() else (self.abs_script_path / vsf_exe_p).resolve())

            general_settings_param = ""
            general_settings_path_str = self.paths_vars["general_settings"].get()
            if general_settings_path_str:
                gs_p = Path(general_settings_path_str)
                resolved_gs_p = gs_p if gs_p.is_absolute() else (self.abs_script_path / gs_p).resolve()
                if resolved_gs_p.is_file():
                    general_settings_param = str(resolved_gs_p)
                else:
                    self.log_queue.put(f"Note: general_settings file '{resolved_gs_p.name if resolved_gs_p else general_settings_path_str}' not found or not a file. VSF will not use the -gs parameter.")

            use_cuda_val = "-uc" if self.settings_vars["use_cuda"].get() else ""
            num_threads_rgb_val = self.settings_vars["number_threads_rgbimages"].get().strip()
            num_threads_txt_val = self.settings_vars["number_threads_txtimages"].get().strip()
            create_cleared_val = "-ccti" if self.settings_vars["create_cleared_text_images"].get() else ""
            start_time_val = self.settings_vars["start_time"].get().strip()
            end_time_val = self.settings_vars["end_time"].get().strip()
            mode_open_video_val = self.settings_vars["mode_open_video"].get()

            if not all_video_files:
                self.log_queue.put(f"DEBUG: _processing_loop_target received an empty video list. This shouldn't happen if start_processing is correct.")
                return

            total_files = len(all_video_files)
            for idx, video_file_path_obj in enumerate(all_video_files): # video_file is now a Path object
                if self.stop_event.is_set():
                    self.log_queue.put("Processing stopped by user.")
                    break

                stem = video_file_path_obj.stem
                output_file_prefix = current_output_dir / f"{stem}_Output"
                self.log_queue.put(f"\n--- Processing file {idx+1}/{total_files}: {video_file_path_obj.name} ---")

                command = [vsf_exe_path]
                if mode_open_video_val: command.append(mode_open_video_val)
                command.extend(["-i", str(video_file_path_obj)]) # Use the Path object directly
                command.extend(["-o", str(output_file_prefix)])
                command.extend(["-r", "-c"]) # -r: Run, -c: Create RGBImages

                if use_cuda_val: command.append(use_cuda_val)
                if num_threads_rgb_val: command.extend(["-nthr", num_threads_rgb_val])
                if num_threads_txt_val: command.extend(["-nocrthr", num_threads_txt_val])
                if create_cleared_val: command.append(create_cleared_val)
                if start_time_val: command.extend(["-s", start_time_val])
                if end_time_val: command.extend(["-e", end_time_val])
                if general_settings_param: command.extend(["-gs", general_settings_param])

                command = [str(c).strip() for c in command if str(c).strip()]


                start_process_time = perf_time()
                self.current_vsf_process = None

                try:
                    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                    self.current_vsf_process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        encoding='utf-8',
                        errors='replace',
                        creationflags=creationflags
                    )

                    stdout_lines = []
                    stderr_lines = []

                    def read_pipe(pipe, output_list, pipe_name):
                        try:
                             if pipe:
                                 for line in iter(pipe.readline, ''):
                                     if self.stop_event.is_set(): break
                                     line_strip = line.strip()
                                     if line_strip:
                                         output_list.append(line_strip)

                        except Exception as e:
                             self.log_queue.put(f"Error reading VSF {pipe_name}: {e}")
                        finally:
                             if pipe: pipe.close()

                    stdout_thread = threading.Thread(target=read_pipe, args=(self.current_vsf_process.stdout, stdout_lines, "stdout"), daemon=True)
                    stderr_thread = threading.Thread(target=read_pipe, args=(self.current_vsf_process.stderr, stderr_lines, "stderr"), daemon=True)
                    stdout_thread.start()
                    stderr_thread.start()

                    stdout_thread.join()
                    stderr_thread.join()
                    return_code = self.current_vsf_process.wait()
                    self.current_vsf_process = None

                    if self.stop_event.is_set():
                        self.log_queue.put(f"Process for {stem} interrupted by user.")
                        break

                    time_used = round(perf_time() - start_process_time)

                    # --- MODIFICATION START: Changed time formatting and log message ---
                    time_str = f"{int(time_used // 3600):02}h:{int((time_used % 3600) // 60):02}m:{int(time_used % 60):02}s"

                    if return_code == 0:
                        self.log_queue.put(f"\nProcess completed: {stem} -> Time Finished: {time_str}")
                    else:

                        if stderr_lines: self.log_queue.put("")
                        # Log time even on error, might be useful
                        self.log_queue.put(f"\nProcess completed: {stem} -> Time Finished: {time_str}")

                    self.log_queue.put("|" + "="*75 + "|")
                    # --- MODIFICATION END ---


                except FileNotFoundError:
                    self.log_queue.put(f"FATAL Error: VideoSubFinder executable not found at '{vsf_exe_path}'. Processing stopped.")
                    if self.winfo_exists(): # Ensure messagebox is parented correctly if GUI still exists
                        self.after(0, lambda: messagebox.showerror("Execution Error", f"VideoSubFinder executable not found:\n{vsf_exe_path}", parent=self))
                    break
                except Exception as e:
                    self.log_queue.put(f"An error occurred while running VSF for {video_file_path_obj.name}: {e}\n{traceback.format_exc()}")
                    if self.current_vsf_process:
                         self.current_vsf_process.kill()
                         self.current_vsf_process = None
                    # if self.winfo_exists():
                    #    self.after(0, lambda: messagebox.showerror("Runtime Error", f"Error processing {video_file_path_obj.name}:\n{e}", parent=self))
                    break # Stop on unexpected errors for a single file
                finally:
                    if self.stop_event.is_set() and self.current_vsf_process:
                         self.log_queue.put(f"Ensuring VSF process for {stem} is terminated due to stop signal.")
                         try: self.current_vsf_process.kill()
                         except: pass # Ignore errors if already dead
                         self.current_vsf_process = None
        except Exception as e:
            self.log_queue.put(f"Critical error in processing loop setup: {e}\n{traceback.format_exc()}")
        finally:
            if hasattr(self, 'winfo_exists') and self.winfo_exists():
                self.after(0, lambda: self._set_controls_state(processing=False))
            self.log_queue.put("--- Video Processing Finished ---")
            self.stop_monitoring()

    # This method is needed by the main class, even if not used in _processing_loop_target
    def _format_time(self, ms):
        if ms < 0: ms = 0
        s, msecs = divmod(ms, 1000)
        mins, secs = divmod(s, 60)
        hrs, mins = divmod(mins, 60)
        if hrs > 0:
            return f"{int(hrs):d}:{int(mins):02d}:{int(secs):02d}.{int(msecs):03d}"
        else:
            return f"{int(mins):02d}:{int(secs):02d}.{int(msecs):03d}"

    def stop_processing(self):
        self.log_message("--- Stopping Video Processing ---")
        self.stop_event.set()

        if self.current_vsf_process and self.current_vsf_process.poll() is None:
            pid = self.current_vsf_process.pid
            self.log_message(f"Attempting to terminate VideoSubFinder process (PID: {pid})...")
            try:
                self.current_vsf_process.terminate()
                try:
                    self.current_vsf_process.wait(timeout=1.0)
                    self.log_message(f"VSF process {pid} terminated gracefully.")
                except subprocess.TimeoutExpired:
                    self.log_message(f"VSF process {pid} did not terminate gracefully, forcing kill...")
                    self.current_vsf_process.kill()
                    self.log_message(f"VSF process {pid} kill signal sent.")
                except Exception as e_wait:
                     self.log_message(f"Error during VSF process wait: {e_wait}. Attempting kill.")
                     self.current_vsf_process.kill()
            except Exception as e:
                self.log_message(f"Error terminating VideoSubFinder process {pid}: {e}")
            self.current_vsf_process = None
        self.stop_monitoring()
        self._set_controls_state(processing=False)
        self.log_message("--- Stop request processed ---")

    def start_monitoring(self, directory_to_monitor):
        if self.observer and self.observer.is_alive():
            self.log_queue.put("Monitoring already active.")
            return

        monitor_path = Path(directory_to_monitor)
        if not monitor_path.is_dir():
            self.log_queue.put(f"Cannot monitor: Directory '{monitor_path}' does not exist or is not a directory.")
            return

        self.stop_monitoring() # Ensure previous observer is stopped

        event_handler = DirectoryMonitorHandler(self.log_queue)
        try:
            self.observer = Observer()
            self.observer.schedule(event_handler, str(monitor_path), recursive=True)
            self.observer.start()
        except Exception as e:
            self.log_queue.put(f"Error starting monitoring: {e}")
            if self.observer and self.observer.is_alive():
                 try:
                     self.observer.stop()
                     self.observer.join()
                 except: pass
            self.observer = None


    def stop_monitoring(self):
        if self.observer and self.observer.is_alive():
            try:
                self.observer.stop()
                self.observer.join(timeout=1)
                if self.observer.is_alive():
                     self.log_queue.put("Warning: Monitoring observer did not stop gracefully after 1 second.")
                else:
                     self.log_queue.put("")
            except Exception as e:
                 self.log_queue.put(f"Error stopping monitoring: {e}")
            finally:
                 self.observer = None


    def on_closing(self):
        if self.crop_editor_window and self.crop_editor_window.winfo_exists():
             try: self.crop_editor_window.destroy()
             except Exception as e: print(f"Error destroying crop window: {e}")

        if self.processing_thread and self.processing_thread.is_alive():
            if messagebox.askyesno("Confirm Exit", "Processing is ongoing. Are you sure you want to exit? This will attempt to stop the current process.", parent=self):
                self.log_message("Exit requested during processing. Stopping...")
                self.stop_event.set()
                self.stop_processing() # This calls stop_monitoring internally

                wait_timeout = 2.0
                if self.processing_thread and self.processing_thread.is_alive():
                     self.log_message(f"Waiting up to {wait_timeout}s for processing thread...")
                     self.processing_thread.join(timeout=wait_timeout)

                if self.processing_thread and self.processing_thread.is_alive():
                     self.log_message("Warning: Processing thread did not exit cleanly.")
                self.destroy()
            else:
                return # User cancelled exit
        else:
            self.stop_event.set() # Ensure any lingering checks stop
            self.stop_monitoring()
            self.destroy()

# --- Main Execution Block ---
if __name__ == "__main__":
    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        ffprobe_cmd = [FFPROBE_PATH, "-version"]
        result = subprocess.run(ffprobe_cmd, capture_output=True, check=True, startupinfo=startupinfo, text=True, encoding='utf-8', errors='replace')
        print(f"ffprobe found: {result.stdout.splitlines()[0]}")
    except FileNotFoundError:
        messagebox.showerror("Dependency Error", f"'{FFPROBE_PATH}' command not found. Please ensure FFmpeg (which includes ffprobe) is installed and its location is added to your system's PATH environment variable.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        messagebox.showerror("Dependency Error", f"'{FFPROBE_PATH}' failed to run. Please ensure FFmpeg is installed correctly.\nCommand: {' '.join(ffprobe_cmd)}\nError: {e}\nOutput:\n{e.stderr}")
        sys.exit(1)
    except Exception as e:
         messagebox.showerror("Dependency Error", f"An unexpected error occurred while checking for ffprobe:\n{e}")
         sys.exit(1)

    app = VideoSubFinderGUI()
    app.mainloop()
