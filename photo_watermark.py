"""
P0-1: 拍照水印叠加工具
拍照时自动叠加 GPS坐标 + 时间戳水印到照片上
参考群报数/今日水印相机做法，生成带不可篡改水印的养护证据照片
"""
import os
import sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


def get_font(size=28):
    """获取跨平台中文字体"""
    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",     # 黑体
        "C:/Windows/Fonts/simsun.ttc",     # 宋体
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


def add_watermark(
    input_path: str,
    output_path: str = None,
    latitude: float = None,
    longitude: float = None,
    timestamp: str = None,
    label: str = "养护巡查",
    opacity: int = 200,
    position: str = "bottom",
) -> str:
    """
    给照片叠加水印。
    
    参数:
        input_path: 原始图片路径
        output_path: 输出路径（默认：原文件名_水印.扩展名）
        latitude/longitude: GPS坐标（若为 None，尝试从EXIF读取）
        timestamp: 时间戳（默认当前时间，格式: YYYY-MM-DD HH:MM:SS）
        label: 标签文字（如"养护巡查"）
        opacity: 水印区域透明度 0-255
        position: "bottom"=底部横条, "top"=顶部横条, "corner"=右下角
    
    返回: output_path
    """
    if not output_path:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_水印{ext}"

    img = Image.open(input_path).convert("RGBA")
    W, H = img.size

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # GPS 坐标格式化
    if latitude is not None and longitude is not None:
        lat_dir = "N" if latitude >= 0 else "S"
        lon_dir = "E" if longitude >= 0 else "W"
        lat_str = f"{abs(latitude):.6f}°{lat_dir}"
        lon_str = f"{abs(longitude):.6f}°{lon_dir}"
        gps_text = f"{lat_str}  {lon_str}"
    else:
        # 尝试从 EXIF 读取 GPS
        try:
            from PIL.ExifTags import GPSTAGS, TAGS
            exif = img.getexif()
            gps_info = {}
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "GPSInfo":
                    for gps_tag_id, gps_value in value.items():
                        gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                        gps_info[gps_tag] = gps_value
            if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                def to_decimal(gps_coords):
                    d, m, s = gps_coords
                    return d + m / 60.0 + s / 3600.0
                lat = to_decimal(gps_info["GPSLatitude"])
                lon = to_decimal(gps_info["GPSLongitude"])
                if gps_info.get("GPSLatitudeRef", "N") == "S":
                    lat = -lat
                if gps_info.get("GPSLongitudeRef", "E") == "W":
                    lon = -lon
                lat_dir = "N" if lat >= 0 else "S"
                lon_dir = "E" if lon >= 0 else "W"
                gps_text = f"{abs(lat):.6f}°{lat_dir}  {abs(lon):.6f}°{lon_dir}"
            else:
                gps_text = "未获取位置"
        except Exception:
            gps_text = "未获取位置"

    # 构建水印文字
    lines = [
        f"{label}  {timestamp}",
        gps_text,
    ]

    # 计算水印区域
    font = get_font(26)
    font_sm = get_font(20)
    fonts = [font, font_sm]
    line_heights = []
    total_h = 0
    max_w = 0
    for line, f in zip(lines, fonts):
        bbox = ImageDraw.Draw(img).textbbox((0, 0), line, font=f)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        line_heights.append((w, h))
        total_h += h + 8
        max_w = max(max_w, w)

    padding = 20
    total_h += padding
    max_w += padding * 2
    bar_h = total_h

    # 确定水印位置
    if position == "bottom":
        y_start = H - bar_h - 15
    elif position == "top":
        y_start = 15
    else:  # corner - 右下角
        y_start = H - bar_h - 15
        max_w = min(max_w, W // 2)

    # 创建水印层
    watermark_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark_layer)

    if position in ("bottom", "top"):
        # 全宽横条
        bar_rect = [(0, y_start), (W, y_start + bar_h)]
        draw.rectangle(bar_rect, fill=(0, 0, 0, opacity))
        # 居中绘制文字
        curr_y = y_start + (bar_h - sum(h + 8 for _, h in line_heights)) // 2
        for line, f, (w, h) in zip(lines, fonts, line_heights):
            x = (W - w) // 2
            draw.text((x, curr_y), line, font=f, fill=(255, 255, 255, 255))
            curr_y += h + 8
    else:  # corner
        x_start = W - max_w - 15
        bar_rect = [(x_start, y_start), (W - 15, y_start + bar_h)]
        draw.rounded_rectangle(bar_rect, radius=10, fill=(0, 0, 0, opacity))
        curr_y = y_start + padding // 2
        for line, f, (w, h) in zip(lines, fonts, line_heights):
            x = x_start + (max_w - padding * 2 - w) // 2 + padding
            draw.text((x, curr_y), line, font=f, fill=(255, 255, 255, 255))
            curr_y += h + 8

    # 合成
    result = Image.alpha_composite(img, watermark_layer)
    result = result.convert("RGB")
    result.save(output_path, quality=95)

    return output_path


def batch_watermark(input_dir: str, output_dir: str = None, **kwargs):
    """批量给目录下所有图片添加水印。"""
    if output_dir is None:
        output_dir = os.path.join(input_dir, "watermarked")
    os.makedirs(output_dir, exist_ok=True)

    results = []
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}
    for f in sorted(os.listdir(input_dir)):
        if not any(f.lower().endswith(e) for e in exts):
            continue
        in_path = os.path.join(input_dir, f)
        out_path = os.path.join(output_dir, f"WM_{os.path.splitext(f)[0]}.jpg")
        out_path = add_watermark(in_path, output_path=out_path, **kwargs)
        results.append((f, out_path))
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="养护巡查照片水印叠加")
    parser.add_argument("input", help="输入图片路径或目录")
    parser.add_argument("-o", "--output", help="输出路径/目录")
    parser.add_argument("--lat", type=float, help="纬度")
    parser.add_argument("--lon", type=float, help="经度")
    parser.add_argument("--label", default="养护巡查", help="水印标签")
    parser.add_argument("--position", default="bottom", choices=["bottom", "top", "corner"])
    args = parser.parse_args()

    if os.path.isdir(args.input):
        results = batch_watermark(args.input, args.output,
                                  latitude=args.lat, longitude=args.lon,
                                  label=args.label, position=args.position)
        for orig, out in results:
            print(f"  {orig} -> {out}")
    else:
        out = add_watermark(args.input, args.output,
                            latitude=args.lat, longitude=args.lon,
                            label=args.label, position=args.position)
        print(f"水印已添加: {out}")
