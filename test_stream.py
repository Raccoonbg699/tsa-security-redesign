import cv2
import time

# ПОСТАВЕТЕ ТУК ВАШИЯ РАБОТЕЩ RTSP URL ОТ VLC
rtsp_url = "ВАШИЯ_РАБОТЕЩ_RTSP_URL"

print(f"Attempting to open stream: {rtsp_url}")

cap = cv2.VideoCapture(rtsp_url)

if not cap.isOpened():
    print("Error: Could not open video stream.")
    print("Please check: 1. Correct RTSP URL. 2. Network connectivity. 3. Firewall rules.")
    print("4. Codec support (try reinstalling opencv-contrib-python).")
else:
    print("Stream opened successfully! Displaying frames (press 'q' to quit)...")
    start_time = time.time()
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error: Failed to read frame or stream ended.")
            break

        frame_count += 1

        # Намаляваме размера на прозореца за показване
        display_frame = cv2.resize(frame, (800, 600)) # Оразмерява кадъра за по-добро показване

        cv2.imshow('Live Stream Test', display_frame)

        # Натиснете 'q' за изход
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

        # Ограничаваме броя кадри в секунда за по-стабилно показване
        time.sleep(0.01) # Пауза от 10ms

        # Изход след 10 секунди, ако не се натисне 'q'
        if time.time() - start_time > 10 and frame_count > 0:
            print("Displaying for 10 seconds. Exiting.")
            break

cap.release()
cv2.destroyAllWindows()
print("Stream test finished.")