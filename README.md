# Batch_VideoSubFinder
This tool allows you to batch process multiple video files using VideoSubFinder with a consistent set of configurations and crop settings.



## Requirements

### 1. Python Version
*   **Recommended**: [Python](https://www.python.org/) 3.7 or newer.

### 2. Install Python Libraries
```bash
pip install customtkinter
pip install Pillow
pip install opencv-python
pip install watchdog
```

### 3. External Tools Required
*   **[VideoSubFinderWXW.exe](https://sourceforge.net/projects/videosubfinder/)**: This is the core command-line tool that the GUI application wraps. You need to have this executable. The GUI will ask for its path.
*   **[ffprobe.exe](https://www.videohelp.com/software/ffmpeg)** (from FFmpeg): This tool is used to get video information (dimensions, duration, FPS) for the visual crop editor.
    *   The script expects `ffprobe.exe` to be located at `ffmpeg/ffprobe.exe` relative to the script's directory (or the directory of the compiled executable).


**How to use:**

1.  **Launch the Application**:
    *   Run the Python script (`Batch_VideoSubFinder.py`).

##Main Tool interface##

![000](https://github.com/user-attachments/assets/f4d1b1c4-3835-4723-85a1-9dd852c07d30)


2.  **Configure Paths (Main Window - "Paths Configuration" section)**:
    *   **VideoSubFinder Executable**: Click "Browse .exe" and select your `VideoSubFinderWXW.exe` file.
    *   **Videos Input Folder**: Click "Browse Folder" and select the directory that contains **all the video files you want to process**. The tool will iterate through common video formats (mp4, mkv, avi, etc.) in this folder. This is the key for multi-video processing.
    *   **General Settings (.cfg)**: Click "Browse .cfg" and select the `general.cfg` file.
        *   This file stores VSF-specific settings, most importantly the **crop percentages**.
        *   If you don't have one, you can specify a path and name (e.g., `my_general.cfg`), and the crop editor or "Save Settings" can help create/populate it.
        *   The crop settings from this file will be applied to **all videos** in the batch.
    *   **Images Output Folder**: Click "Browse Folder" to choose where the output images (RGBImages, TXTImages) will be saved. If left blank, a default folder named `Output_Videos_Images` will be created in the script's directory. Each video will typically get its own subfolder within this output path (e.g., `VideoName_Output/RGBImages`).

3.  **Configure VideoSubFinder Settings (Main Window - "VideoSubFinder Settings" section)**:
    *   These settings are loaded from `Settings.ini` (created automatically with defaults if not present).
    *   Adjust settings like:
        *   `Video Open Mode` (ffmpeg or ocv)
        *   `Create Cleared Text Images` (checkbox for `-ccti` flag)
        *   `Use CUDA` (checkbox for `-uc` flag if you have a compatible NVIDIA GPU)
        *   `Start Time` / `End Time` (e.g., `00:01:30.000`): If you want to process only a specific segment of the videos. Leave blank to process the entire duration.
        *   `Number Threads for RGB Images` / `Number Threads for TXT Images`: number of threads used for RGB Images & TXT Images .
    *   These settings will be applied uniformly to **all videos** processed in the batch.

##Edit Crop Visually interface##

![vsf4](https://github.com/user-attachments/assets/0e174f6a-488e-44af-b331-bd6475342703)


4.  **Configure Image Crop Area (Crucial for accurate subtitle detection)**:
    *   The crop percentages (Top, Bottom, Left, Right) displayed in the "Image Crop Area" section are loaded from the `general.cfg` file you specified.
    *   **To visually set or adjust crop settings**:
        1.  Click the **"Edit Crop Visually"** button.
        2.  A new "Visual Crop Region Editor" window will open.
        3.  It will attempt to load the first video file from your "Videos Input Folder". If it doesn't, or you want to use a different video from that folder as a reference, click "Open Video" in the editor window.
        4.  Use the time slider to navigate to a frame in the video where subtitles are visible and representative of their typical position.
        5.  **Drag the green lines** on the video preview to define the area where subtitles appear. The area *outside* these lines is what VSF effectively "crops" or ignores for subtitle detection.
            *   The percentage values displayed (e.g., "Crop Top: 0.258929") are in the format VSF expects for `general.cfg`.
        6.  Once satisfied, click **"Save to general.cfg & Close"**. This action writes the adjusted crop percentages directly to the `general.cfg` file specified in the main window.
        7.  The main window's crop percentage display should update to reflect these changes.

5.  **Save All Settings (Recommended)**:
    *   In the main window, click the **"Save Settings"** button.
    *   This saves:
        *   Path configurations and VSF settings (from step 2 & 3) to `Settings.ini`.
        *   The currently displayed crop percentages (which should now match what you set in the visual editor) back to the `general.cfg` file, ensuring consistency.

6.  **Start Processing Multiple Videos**:
    *   Click the **"Start Processing"** button.
    *   The application will:
        *   Scan the "Videos Input Folder" for all supported video files.
        *   For **each video file found**, it will sequentially run `VideoSubFinderWXW.exe` using:
            *   The configured path to `VideoSubFinderWXW.exe`.
            *   The current video file as input.
            *   The specified output path (creating subdirectories for each video's images).
            *   All selected VSF command-line options (CUDA, threads, time range, etc.).
            *   The `-gs path/to/your/general.cfg` argument, so VSF uses the crop settings you defined.
    *   The "Output Log" will display progress, including which file is being processed, VSF's own console output, and messages about image creation.


7.  **Stop Processing (If Necessary)**:
    *   If you need to interrupt the batch process, click the **"Stop Processing"** button. This will attempt to terminate the currently running `VideoSubFinderWXW.exe` instance and will not start processing any subsequent videos in the queue.

8. **Review Output**:
    *   After processing is complete (or stopped), navigate to your "Images Output Folder". You should find subfolders for each processed video (e.g., `MyVideo1_Output`, `MyVideo2_Output`), and inside them, folders like `RGBImages` (and `TXTImages` if enabled) containing the extracted subtitle images.

**Important Notes for Multi-Video Processing:**

*   **Uniform Settings**: All videos in a single batch run will use the *same* VSF settings (CUDA, threads, etc.) and the *same* crop parameters defined in the `general.cfg`.
*   **Varying Crop Needs**: If different videos require significantly different crop areas, you would need to:
    1.  Group videos with similar crop needs.
    2.  Adjust `general.cfg` (using the "Edit Crop Visually" tool) for the first group.
    3.  Process the first group.
    4.  Re-adjust `general.cfg` for the next group.
    5.  Process the next group, and so on.
    The GUI itself doesn't manage per-video crop profiles within a single batch operation.
*   **Output Organization**: The tool is designed to create an output prefix based on the video's stem (e.g., `video_filename_Output`), so images from different videos are kept separate within your main output directory.

By following these steps, you can efficiently process a large number of videos to extract subtitle images using Video Sub Finder V2.0.3. The visual crop editor is a key feature to help you define accurate subtitle regions for better results.
