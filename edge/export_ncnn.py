"""Exports the models to NCNN, the fastest format on the Raspberry Pi CPU.

    python export_ncnn.py --imgsz 640
Then in config/edge.yaml:
    pose: yolo11n-pose_ncnn_model
    detect: yolo11n_ncnn_model
"""
import argparse

from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument("--imgsz", type=int, default=640)
a = ap.parse_args()
for name in ("yolo11n-pose.pt", "yolo11n.pt"):
    print("export :", YOLO(name).export(format="ncnn", imgsz=a.imgsz))
