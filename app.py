
import streamlit as st
import cv2
import numpy as np

def main():
    st.title("Webcam Live Stream")

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        st.error("Cannot open camera")
        return

    frame_placeholder = st.empty()

    while True:
        ret, frame = cap.read()
        if not ret:
            st.error("Failed to capture frame")
            break
        
        # Convert the frame to RGB
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        frame_placeholder.image(frame, channels="RGB")

    cap.release()

if __name__ == "__main__":
    main()
