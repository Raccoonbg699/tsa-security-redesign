TSA-Security - Advanced IP Camera Viewer
TSA-Security is a powerful and intuitive desktop application for monitoring and recording video from IP cameras, developed with Python and PyQt5. It offers a rich set of features, making it suitable for both home and professional use.

✨ Key Features
Live View: Monitor up to 9 cameras simultaneously in a flexible grid that adapts to your window size.

Camera Management: Easily add, edit, and remove cameras.

Configuration Persistence: All your cameras are automatically saved and loaded on every launch.

Network Scanning: Automatically discover IP cameras on your local network.

Motion Detection:

Automatic Recording: Record video only when motion is detected.

Adjustable Sensitivity: Fine-tune how much change is needed to trigger a recording.

Region of Interest (ROI): Define a specific area of the frame to monitor for motion, ignoring unnecessary elements like timestamps.

Post-Motion Recording: Set how many seconds the recording should continue after motion has stopped.

PTZ Control: Full Pan, Tilt, and Zoom control for cameras that support the ONVIF protocol, with adjustable movement speed.

Recording Manager:

Organized Structure: All recordings and snapshots are stored in the Videos\TSA-Security folder, organized into subfolders for each camera.

Built-in File Browser: Browse, play, delete, and open the recordings folder directly from the application.

Modern UI: A clean, minimalist, and intuitive dark-themed design.

🚀 Installation and Startup
The application is designed to be as easy to launch as possible.

Requirements:
Python 3: Ensure you have Python 3 installed. You can download it from python.org.

ONVIF Library (for PTZ): To use the PTZ controls, an additional library is required.

Launching:
The easiest way to start the application is by using the provided start.bat file.

Clone or download the files from this repository into a folder of your choice.

Make sure the camera_viewer.py and start.bat files are in the same folder.

Simply double-click on start.bat.

The script will automatically:

Check if you have Python installed.

Check if the required libraries (PyQt5, opencv-python, onvif-zeep) are installed.

If a library is missing, it will install it automatically.

Launch the TSA-Security application.

🛠️ Usage
Adding a Camera:

Click the "Add Camera" button.

Fill in the name, RTSP URL, and username/password (if you want to use PTZ).

Motion Detection:

Select a camera from the list.

In the "Motion Detection" panel, check the "Enable Motion Detection" box.

Adjust the sensitivity and post-motion recording time.

Click "Define ROI" and draw a rectangle on the video feed with your mouse to define the monitoring area.

Reviewing Recordings:

Go to the "Recordings" tab.

Select a camera folder on the left to see its recordings on the right.

Use the buttons to play, delete, or open the file's location in Windows Explorer.

📦 Dependencies
The start.bat script automatically installs the following Python libraries:

PyQt5

opencv-python

onvif-zeep

🤝 Contributing
Contributions to the project are welcome! If you have ideas for new features or improvements, please open an issue or submit a pull request.