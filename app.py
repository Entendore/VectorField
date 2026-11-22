import sys
import math
import random
import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QPointF, QRect, QRectF
from PySide6.QtGui import QImage, QPixmap, QColor, QPainter, QPen, QFont, QLinearGradient, QRadialGradient
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QProgressBar, QLabel, 
                               QSlider, QComboBox, QColorDialog, QGroupBox, 
                               QFormLayout, QLineEdit, QScrollArea, QCheckBox)

# --- Configuration ---
RENDER_WIDTH = 1080
RENDER_HEIGHT = 1920

# --- Vector Field Presets ---
FIELD_PRESETS = {
    "Cosmic Flow": ("Direct", "sin(y*3 + t) - cos(x*2 - t*0.8)", "cos(x*3 - t) + sin(y*2 + t*0.8)"),
    "Vortex": ("Direct", "-y * cos(t*0.5) + x * sin(t*0.5)", "x * cos(t*0.5) + y * sin(t*0.5)"),
    "Chaotic Attractor": ("Direct", "sin(y*2) - cos(x*3 + t)", "cos(x*2) + sin(y*3 - t)"),
    "Polar Spiral": ("Direct", "-sin(theta + t) * r", "cos(theta + t) * r"),
    "Black Hole (Polar)": ("Direct", "-cos(theta) / (r + 0.1)", "-sin(theta) / (r + 0.1)"),
    "Mandelbrot Flow": ("Direct", "z.real**2 - z.imag**2 + sin(t)", "2*z.real*z.imag + cos(t)"),
    "Dipole Potential": ("Potential", "x / (x**2 + y**2 + 0.1)**1.5", ""),
    "Saddle Potential": ("Potential", "x**2 - y**2 + sin(t)*x", ""),
    "Crater Potential": ("Potential", "-exp(-x**2*5) - exp(-y**2*5) + x*y", "")
}

def get_palette_color(val, palette):
    val = max(0.0, min(1.0, val))
    if palette == "Rainbow":
        return QColor.fromHsvF(val, 1.0, 1.0)
    elif palette == "Cyberpunk":
        if val < 0.5:
            t = val * 2
            r, g, b = 1.0 - t, 0.0, 1.0
        else:
            t = (val - 0.5) * 2
            r, g, b = 0.0, t, 1.0
        return QColor(int(r*255), int(g*255), int(b*255))
    elif palette == "Inferno":
        if val < 0.5:
            t = val * 2
            r, g, b = t, 0.0, 0.0
        else:
            t = (val - 0.5) * 2
            r, g, b = 1.0, t, 0.0
        return QColor(int(r*255), int(g*255), int(b*255))
    elif palette == "Ocean":
        if val < 0.5:
            t = val * 2
            r, g, b = 0.0, t, 0.5 + t*0.5
        else:
            t = (val - 0.5) * 2
            r, g, b = t, 1.0, 1.0
        return QColor(int(r*255), int(g*255), int(b*255))
    return QColor(255, 255, 255)

class Particle:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.reset(initial=True)

    def reset(self, initial=False):
        self.x = random.uniform(0, self.width)
        self.y = random.uniform(0, self.height)
        self.life = random.randint(50, 200)
        if initial:
            self.life = random.randint(0, 200)

    def update(self, t, speed, mode, code1, code2, eval_env, zoom, pan_x, pan_y):
        # Apply Camera Transformations BEFORE math
        nx = ((self.x / self.width) * 2.0 - 1.0) / zoom + pan_x
        ny = ((self.y / self.height) * 2.0 - 1.0) / zoom + pan_y
        
        r = math.hypot(nx, ny)
        theta = math.atan2(ny, nx)
        z = complex(nx, ny)

        eval_env['x'] = nx
        eval_env['y'] = ny
        eval_env['t'] = t
        eval_env['r'] = r
        eval_env['theta'] = theta
        eval_env['z'] = z

        try:
            if mode == "Direct":
                dx = eval(code1, eval_env)
                dy = eval(code2, eval_env)
            elif mode == "Potential":
                eval_env['x'] = nx + 0.001
                u_xp = eval(code1, eval_env)
                eval_env['x'] = nx - 0.001
                u_xm = eval(code1, eval_env)
                dx = (u_xp - u_xm) / 0.002
                
                eval_env['x'] = nx
                eval_env['y'] = ny + 0.001
                u_yp = eval(code1, eval_env)
                eval_env['y'] = ny - 0.001
                u_ym = eval(code1, eval_env)
                dy = (u_yp - u_ym) / 0.002
                eval_env['y'] = ny 
            else:
                dx, dy = 0, 0

            if math.isnan(dx) or math.isinf(dx): dx = 0
            if math.isnan(dy) or math.isinf(dy): dy = 0
        except Exception:
            dx, dy = 0, 0

        prev_x, prev_y = self.x, self.y
        self.x += dx * speed
        self.y += dy * speed
        self.life -= 1

        if self.life <= 0 or self.x < -50 or self.x > self.width + 50 or self.y < -50 or self.y > self.height + 50:
            self.reset()

        mag = math.hypot(dx, dy)
        return prev_x, prev_y, self.x, self.y, mag, r


class VectorFieldCanvas(QWidget):
    def __init__(self, width, height):
        super().__init__()
        self.render_width = width
        self.render_height = height
        
        self.setFixedSize(360, 640)
        
        self.buffer = QImage(self.render_width, self.render_height, QImage.Format_ARGB32)
        self.bg_color = QColor(10, 10, 10)
        self.buffer.fill(self.bg_color)
        
        self.settings = {
            'particle_count': 1500,
            'speed': 3.0,
            'trail_fade': 15,
            'time_scale': 0.02,
            'line_width': 2,
            'opacity': 200,
            'field_mode': "Direct",
            'eq1': FIELD_PRESETS["Cosmic Flow"][1],
            'eq2': FIELD_PRESETS["Cosmic Flow"][2],
            'show_overlay': True,
            'glow': True,
            'vignette': True,
            'comet': True,
            'color_source': "Velocity",
            'palette': "Cyberpunk",
            'bg_style': "Solid",
            'zoom': 1.0,
            'pan_x': 0.0,
            'pan_y': 0.0
        }
        
        self.eval_env = {
            "__builtins__": {},
            "sin": math.sin, "cos": math.cos, "tan": math.tan,
            "sqrt": math.sqrt, "abs": abs, "pi": math.pi,
            "exp": math.exp, "log": math.log, "pow": pow,
            "atan2": math.atan2, "hypot": math.hypot,
            "floor": math.floor, "ceil": math.ceil,
            "e": math.e, "tau": math.tau
        }
        
        self.code1 = compile(self.settings['eq1'], "<string>", "eval")
        self.code2 = compile(self.settings['eq2'], "<string>", "eval")
        
        self.particles = []
        self.update_particle_count()
        
        self.t = 0.0
        self.is_playing = True
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def update_equations(self, eq1, eq2):
        try:
            self.code1 = compile(eq1, "<string>", "eval")
            if eq2:
                self.code2 = compile(eq2, "<string>", "eval")
            self.settings['eq1'] = eq1
            self.settings['eq2'] = eq2
            return True
        except SyntaxError:
            return False

    def update_particle_count(self):
        target = self.settings['particle_count']
        if len(self.particles) < target:
            for _ in range(target - len(self.particles)):
                self.particles.append(Particle(self.render_width, self.render_height))
        elif len(self.particles) > target:
            self.particles = self.particles[:target]

    def draw_overlay(self, painter):
        painter.setPen(QColor(255, 255, 255, 230))
        font = QFont("Consolas", 28, QFont.Bold)
        painter.setFont(font)
        
        mode = self.settings['field_mode']
        eq1 = self.settings['eq1']
        eq2 = self.settings['eq2']
        
        if mode == "Direct":
            text1 = f"∂x/∂t = {eq1}"
            text2 = f"∂y/∂t = {eq2}"
        elif mode == "Potential":
            text1 = f"U(x,y,t) = {eq1}"
            text2 = "Vector Field = ∇U"
        else:
            text1, text2 = "", ""
            
        rect1 = painter.boundingRect(QRect(40, 60, 1000, 60), Qt.AlignLeft, text1)
        rect2 = painter.boundingRect(QRect(40, 130, 1000, 60), Qt.AlignLeft, text2)
        
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(rect1.x()-15, rect1.y()-10, rect1.width()+30, rect1.height()+20), 10, 10)
        painter.drawRoundedRect(QRectF(rect2.x()-15, rect2.y()-10, rect2.width()+30, rect2.height()+20), 10, 10)
        
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(rect1, Qt.AlignLeft, text1)
        painter.drawText(rect2, Qt.AlignLeft, text2)

    def draw_vignette(self, painter):
        grad = QLinearGradient(0, 0, 0, self.render_height)
        color_top = QColor(0, 0, 0, 180)
        color_mid = QColor(0, 0, 0, 0)
        color_bot = QColor(0, 0, 0, 180)
        grad.setColorAt(0.0, color_top)
        grad.setColorAt(0.25, color_mid)
        grad.setColorAt(0.75, color_mid)
        grad.setColorAt(1.0, color_bot)
        painter.fillRect(self.buffer.rect(), grad)

    def draw_background(self, painter):
        s = self.settings
        if s['bg_style'] == "Solid":
            painter.fillRect(self.buffer.rect(), self.bg_color)
        elif s['bg_style'] == "Radial Gradient":
            grad = QRadialGradient(self.render_width/2, self.render_height/2, self.render_height/1.5)
            # Make center slightly brighter version of bg_color
            center_color = QColor(self.bg_color)
            center_color = center_color.lighter(150)
            grad.setColorAt(0.0, center_color)
            grad.setColorAt(1.0, self.bg_color)
            painter.fillRect(self.buffer.rect(), grad)
        elif s['bg_style'] == "Math Grid":
            painter.fillRect(self.buffer.rect(), self.bg_color)
            pen = QPen(QColor(255, 255, 255, 15), 1) # Very faint lines
            painter.setPen(pen)
            step = 108
            for x in range(0, self.render_width, step):
                painter.drawLine(x, 0, x, self.render_height)
            for y in range(0, self.render_height, step):
                painter.drawLine(0, y, self.render_width, y)

    def draw_frame(self, painter, t, particles):
        s = self.settings
        
        # 1. Fade Trails (Uses solid bg color for fading regardless of bg style)
        fade_color = QColor(self.bg_color)
        fade_color.setAlpha(s['trail_fade'])
        painter.fillRect(self.buffer.rect(), fade_color)
        
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 2. Draw Particles
        for p in particles:
            prev_x, prev_y, curr_x, curr_y, mag, r = p.update(
                t, s['speed'], s['field_mode'], self.code1, self.code2, self.eval_env,
                s['zoom'], s['pan_x'], s['pan_y']
            )
            
            # Determine Color Value
            if s['color_source'] == "Velocity":
                val = min(mag / 5.0, 1.0)
            elif s['color_source'] == "Radius":
                val = min(r / 1.5, 1.0)
            elif s['color_source'] == "Time":
                val = (t * 0.5) % 1.0
            else: # Angle
                angle = math.atan2(curr_y - prev_y, curr_x - prev_x)
                val = (angle + math.pi) / (2 * math.pi)
                
            color = get_palette_color(val, s['palette'])
            color.setAlpha(s['opacity'])
            
            # Glow underlay
            if s['glow']:
                glow_color = QColor(color)
                glow_color.setAlpha(int(s['opacity'] * 0.25))
                painter.setPen(QPen(glow_color, s['line_width'] * 3))
                painter.drawLine(QPointF(prev_x, prev_y), QPointF(curr_x, curr_y))
            
            # Main Line (Comet Tail logic)
            if s['comet']:
                # Tail (Thinner, fainter)
                tail_color = QColor(color)
                tail_color.setAlpha(int(s['opacity'] * 0.6))
                painter.setPen(QPen(tail_color, s['line_width']))
                painter.drawLine(QPointF(prev_x, prev_y), QPointF(curr_x, curr_y))
                
                # Head (Thicker, brighter)
                head_color = QColor(color)
                head_color.setAlpha(255) # Full opacity
                painter.setPen(QPen(head_color, s['line_width'] * 1.5))
                painter.drawPoint(QPointF(curr_x, curr_y))
            else:
                painter.setPen(QPen(color, s['line_width']))
                painter.drawLine(QPointF(prev_x, prev_y), QPointF(curr_x, curr_y))
        
        # 3. Vignette
        if s['vignette']:
            self.draw_vignette(painter)
            
        # 4. Text Overlay
        if s['show_overlay']:
            self.draw_overlay(painter)

    def tick(self):
        if not self.is_playing:
            return
            
        self.t += self.settings['time_scale']
        
        painter = QPainter(self.buffer)
        self.draw_frame(painter, self.t, self.particles)
        painter.end()
        
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), QPixmap.fromImage(self.buffer), self.buffer.rect())

    def get_current_frame(self):
        img = self.buffer.convertToFormat(QImage.Format_RGBA8888)
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))
        arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return arr_bgr


class ExportThread(QThread):
    progress = Signal(int)
    finished = Signal(str)

    def __init__(self, canvas, duration, fps):
        super().__init__()
        self.canvas = canvas
        self.duration = duration
        self.fps = fps
        self.total_frames = fps * duration

    def run(self):
        filename = f"youtube_short_{self.duration}s_{self.fps}fps.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(filename, fourcc, self.fps, (RENDER_WIDTH, RENDER_HEIGHT))
        
        was_playing = self.canvas.is_playing
        self.canvas.is_playing = False
        
        # Draw the static background once before rendering frames
        painter = QPainter(self.canvas.buffer)
        self.canvas.draw_background(painter)
        painter.end()
        
        local_t = 0.0
        settings = self.canvas.settings.copy()
        
        render_canvas = VectorFieldCanvas(RENDER_WIDTH, RENDER_HEIGHT)
        render_canvas.settings = settings
        render_canvas.code1 = self.canvas.code1
        render_canvas.code2 = self.canvas.code2
        render_canvas.eval_env = self.canvas.eval_env
        render_canvas.bg_color = self.canvas.bg_color
        
        # Draw static bg for render canvas
        p = QPainter(render_canvas.buffer)
        render_canvas.draw_background(p)
        p.end()
        
        render_canvas.update_particle_count()
        
        for frame_idx in range(self.total_frames):
            local_t += settings['time_scale']
            
            painter = QPainter(render_canvas.buffer)
            render_canvas.draw_frame(painter, local_t, render_canvas.particles)
            painter.end()
            
            # Copy to UI canvas to show progress
            p2 = QPainter(self.canvas.buffer)
            p2.drawImage(0, 0, render_canvas.buffer)
            p2.end()
            self.canvas.update()
            
            frame = render_canvas.get_current_frame()
            out.write(frame)
            
            progress_val = int(((frame_idx + 1) / self.total_frames) * 100)
            self.progress.emit(progress_val)
            self.msleep(5)

        out.release()
        self.canvas.is_playing = was_playing
        self.finished.emit(filename)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shorts Vector Field Studio - Advanced Cinematics")
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QLabel { color: #e0e0e0; }
            QGroupBox { 
                color: #ffffff; border: 1px solid #333333; border-radius: 5px; 
                margin-top: 12px; padding-top: 12px; font-weight: bold;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QPushButton { 
                background-color: #2d2d2d; color: white; border: 1px solid #444444; 
                border-radius: 4px; padding: 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #3a3a3a; }
            QPushButton:disabled { background-color: #1a1a1a; color: #555555; }
            QComboBox, QSpinBox, QLineEdit { 
                background-color: #2d2d2d; color: white; border: 1px solid #444444; 
                border-radius: 4px; padding: 4px;
            }
            QSlider::groove:horizontal { border: 1px solid #444; height: 4px; background: #2d2d2d; }
            QSlider::handle:horizontal { background: #ff0000; width: 14px; margin: -6px 0; border-radius: 7px; }
            QScrollArea { border: none; background-color: #121212; }
            QCheckBox { color: white; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        canvas_layout = QVBoxLayout()
        canvas_layout.setAlignment(Qt.AlignCenter)
        self.canvas = VectorFieldCanvas(RENDER_WIDTH, RENDER_HEIGHT)
        canvas_layout.addWidget(self.canvas)
        main_layout.addLayout(canvas_layout, stretch=2)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setFixedWidth(340)
        scroll.setWidget(controls_widget)
        main_layout.addWidget(scroll, stretch=1)

        self.setup_ui(controls_layout)
        controls_layout.addStretch()

    def setup_ui(self, parent_layout):
        # 1. Math Engine Group
        eq_group = QGroupBox("Vector Field Equations")
        eq_layout = QFormLayout(eq_group)
        
        self.combo_field_type = QComboBox()
        self.combo_field_type.addItems(["Direct (dx, dy)", "Potential Field (∇U)"])
        self.combo_field_type.currentIndexChanged.connect(self.toggle_field_type)
        eq_layout.addRow("Mode:", self.combo_field_type)

        self.combo_presets = QComboBox()
        self.combo_presets.addItems(list(FIELD_PRESETS.keys()))
        self.combo_presets.currentTextChanged.connect(self.load_preset)
        eq_layout.addRow("Presets:", self.combo_presets)

        self.lbl_eq_help = QLabel(
            "Vars: <b>x, y, r, theta, z, t</b>\n"
            "Funcs: sin, cos, sqrt, atan2, pi, e"
        )
        self.lbl_eq_help.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        eq_layout.addRow(self.lbl_eq_help)

        self.lbl_eq1 = QLabel("dx/dt =")
        self.input_eq1 = QLineEdit()
        self.input_eq1.setText(FIELD_PRESETS["Cosmic Flow"][1])
        eq_layout.addRow(self.lbl_eq1, self.input_eq1)

        self.lbl_eq2 = QLabel("dy/dt =")
        self.input_eq2 = QLineEdit()
        self.input_eq2.setText(FIELD_PRESETS["Cosmic Flow"][2])
        eq_layout.addRow(self.lbl_eq2, self.input_eq2)

        self.btn_apply_eq = QPushButton("Apply Equations")
        self.btn_apply_eq.clicked.connect(self.apply_equations)
        eq_layout.addRow(self.btn_apply_eq)
        
        self.lbl_eq_status = QLabel("Equations compiled successfully.")
        self.lbl_eq_status.setStyleSheet("color: #4caf50; font-size: 10px;")
        eq_layout.addRow(self.lbl_eq_status)

        self.check_overlay = QCheckBox("Show Equations on Video")
        self.check_overlay.setChecked(True)
        self.check_overlay.stateChanged.connect(self.update_visuals)
        eq_layout.addRow("", self.check_overlay)

        parent_layout.addWidget(eq_group)

        # 2. Camera & Background Group (NEW)
        cam_group = QGroupBox("Camera & Background")
        cam_layout = QFormLayout(cam_group)

        self.combo_bg_style = QComboBox()
        self.combo_bg_style.addItems(["Solid", "Radial Gradient", "Math Grid"])
        self.combo_bg_style.currentTextChanged.connect(self.update_visuals)
        cam_layout.addRow("Background:", self.combo_bg_style)

        self.btn_bg_color = QPushButton("Pick Background Color")
        self.btn_bg_color.clicked.connect(self.pick_bg_color)
        cam_layout.addRow("BG Color:", self.btn_bg_color)

        self.slider_zoom = QSlider(Qt.Horizontal)
        self.slider_zoom.setRange(10, 50) # 1.0 to 5.0
        self.slider_zoom.setValue(10)
        self.slider_zoom.valueChanged.connect(self.update_visuals)
        self.lbl_zoom = QLabel("1.0x")
        cam_layout.addRow("Zoom:", self.slider_zoom)
        cam_layout.addRow("", self.lbl_zoom)

        self.slider_pan_x = QSlider(Qt.Horizontal)
        self.slider_pan_x.setRange(-100, 100)
        self.slider_pan_x.setValue(0)
        self.slider_pan_x.valueChanged.connect(self.update_visuals)
        self.lbl_pan_x = QLabel("0.00")
        cam_layout.addRow("Pan X:", self.slider_pan_x)
        cam_layout.addRow("", self.lbl_pan_x)

        self.slider_pan_y = QSlider(Qt.Horizontal)
        self.slider_pan_y.setRange(-100, 100)
        self.slider_pan_y.setValue(0)
        self.slider_pan_y.valueChanged.connect(self.update_visuals)
        self.lbl_pan_y = QLabel("0.00")
        cam_layout.addRow("Pan Y:", self.slider_pan_y)
        cam_layout.addRow("", self.lbl_pan_y)

        parent_layout.addWidget(cam_group)

        # 3. Cinematic Visualization Group
        vis_group = QGroupBox("Cinematic Visualization")
        vis_layout = QFormLayout(vis_group)

        self.combo_palette = QComboBox()
        self.combo_palette.addItems(["Rainbow", "Cyberpunk", "Inferno", "Ocean"])
        self.combo_palette.currentTextChanged.connect(self.update_visuals)
        vis_layout.addRow("Color Palette:", self.combo_palette)

        self.combo_color_src = QComboBox()
        self.combo_color_src.addItems(["Velocity", "Angle", "Radius", "Time"])
        self.combo_color_src.currentTextChanged.connect(self.update_visuals)
        vis_layout.addRow("Color Source:", self.combo_color_src)

        self.slider_particles = QSlider(Qt.Horizontal)
        self.slider_particles.setRange(100, 5000)
        self.slider_particles.setValue(1500)
        self.slider_particles.valueChanged.connect(self.update_visuals)
        self.lbl_particles = QLabel("1500")
        vis_layout.addRow("Particles:", self.slider_particles)
        vis_layout.addRow("", self.lbl_particles)

        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setRange(1, 100)
        self.slider_speed.setValue(30)
        self.slider_speed.valueChanged.connect(self.update_visuals)
        self.lbl_speed = QLabel("3.0")
        vis_layout.addRow("Speed:", self.slider_speed)
        vis_layout.addRow("", self.lbl_speed)

        self.slider_trail = QSlider(Qt.Horizontal)
        self.slider_trail.setRange(1, 100)
        self.slider_trail.setValue(15)
        self.slider_trail.valueChanged.connect(self.update_visuals)
        self.lbl_trail = QLabel("15 (Lower=Longer)")
        vis_layout.addRow("Trail Fade:", self.slider_trail)
        vis_layout.addRow("", self.lbl_trail)

        self.slider_width = QSlider(Qt.Horizontal)
        self.slider_width.setRange(1, 10)
        self.slider_width.setValue(2)
        self.slider_width.valueChanged.connect(self.update_visuals)
        self.lbl_width = QLabel("2px")
        vis_layout.addRow("Line Width:", self.slider_width)
        vis_layout.addRow("", self.lbl_width)

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(50, 255)
        self.slider_opacity.setValue(200)
        self.slider_opacity.valueChanged.connect(self.update_visuals)
        self.lbl_opacity = QLabel("200")
        vis_layout.addRow("Brightness:", self.slider_opacity)
        vis_layout.addRow("", self.lbl_opacity)

        self.slider_time = QSlider(Qt.Horizontal)
        self.slider_time.setRange(1, 100)
        self.slider_time.setValue(20)
        self.slider_time.valueChanged.connect(self.update_visuals)
        self.lbl_time = QLabel("0.02")
        vis_layout.addRow("Time Scale:", self.slider_time)
        vis_layout.addRow("", self.lbl_time)

        self.check_glow = QCheckBox("Neon Glow Effect")
        self.check_glow.setChecked(True)
        self.check_glow.stateChanged.connect(self.update_visuals)
        vis_layout.addRow("", self.check_glow)

        self.check_comet = QCheckBox("Comet Tails (3D Effect)")
        self.check_comet.setChecked(True)
        self.check_comet.stateChanged.connect(self.update_visuals)
        vis_layout.addRow("", self.check_comet)

        self.check_vignette = QCheckBox("Cinematic Vignette")
        self.check_vignette.setChecked(True)
        self.check_vignette.stateChanged.connect(self.update_visuals)
        vis_layout.addRow("", self.check_vignette)

        parent_layout.addWidget(vis_group)

        # 4. Export Group
        export_group = QGroupBox("Export Settings")
        export_layout = QFormLayout(export_group)

        self.combo_duration = QComboBox()
        self.combo_duration.addItems(["5 seconds", "10 seconds", "15 seconds", "30 seconds"])
        export_layout.addRow("Duration:", self.combo_duration)

        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["30 FPS", "60 FPS"])
        export_layout.addRow("Frame Rate:", self.combo_fps)

        parent_layout.addWidget(export_group)

        # 5. Action Buttons
        self.btn_play_pause = QPushButton("⏸ Pause Preview")
        self.btn_play_pause.setFixedHeight(40)
        self.btn_play_pause.clicked.connect(self.toggle_play)
        parent_layout.addWidget(self.btn_play_pause)

        self.btn_export_png = QPushButton("🖼 Export Frame to PNG")
        self.btn_export_png.setFixedHeight(40)
        self.btn_export_png.clicked.connect(self.export_png)
        parent_layout.addWidget(self.btn_export_png)

        self.btn_export_mp4 = QPushButton("🎬 Render Video MP4")
        self.btn_export_mp4.setFixedHeight(45)
        self.btn_export_mp4.setStyleSheet("background-color: #cc0000; font-size: 14px;")
        self.btn_export_mp4.clicked.connect(self.start_export)
        parent_layout.addWidget(self.btn_export_mp4)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        parent_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready. Adjust settings and Render.")
        self.status_label.setWordWrap(True)
        parent_layout.addWidget(self.status_label)

        self.update_visuals() # Initialize labels

    def toggle_field_type(self, index):
        if index == 0: # Direct
            self.canvas.settings['field_mode'] = "Direct"
            self.lbl_eq1.setText("dx/dt =")
            self.lbl_eq2.setText("dy/dt =")
            self.input_eq2.setVisible(True)
            self.lbl_eq2.setVisible(True)
        else: # Potential
            self.canvas.settings['field_mode'] = "Potential"
            self.lbl_eq1.setText("U(x,y,t) =")
            self.lbl_eq2.setText("")
            self.input_eq2.setVisible(False)
            self.lbl_eq2.setVisible(False)
        self.apply_equations()

    def load_preset(self, preset_name):
        if preset_name in FIELD_PRESETS:
            mode, eq1, eq2 = FIELD_PRESETS[preset_name]
            
            self.combo_field_type.blockSignals(True)
            self.combo_field_type.setCurrentIndex(0 if mode == "Direct" else 1)
            self.combo_field_type.blockSignals(False)
            
            if mode == "Direct":
                self.lbl_eq1.setText("dx/dt =")
                self.lbl_eq2.setText("dy/dt =")
                self.input_eq2.setVisible(True)
                self.lbl_eq2.setVisible(True)
            else:
                self.lbl_eq1.setText("U(x,y,t) =")
                self.lbl_eq2.setText("")
                self.input_eq2.setVisible(False)
                self.lbl_eq2.setVisible(False)
                
            self.canvas.settings['field_mode'] = mode
            self.input_eq1.setText(eq1)
            self.input_eq2.setText(eq2)
            self.apply_equations()

    def apply_equations(self):
        eq1 = self.input_eq1.text()
        eq2 = self.input_eq2.text() if self.input_eq2.isVisible() else ""
        success = self.canvas.update_equations(eq1, eq2)
        if success:
            self.lbl_eq_status.setText("✅ Equations compiled successfully.")
            self.lbl_eq_status.setStyleSheet("color: #4caf50; font-size: 10px;")
        else:
            self.lbl_eq_status.setText("❌ Syntax Error in equations!")
            self.lbl_eq_status.setStyleSheet("color: #f44336; font-size: 10px;")

    def update_visuals(self):
        s = self.canvas.settings
        s['particle_count'] = self.slider_particles.value()
        s['speed'] = self.slider_speed.value() / 10.0
        s['trail_fade'] = self.slider_trail.value()
        s['time_scale'] = self.slider_time.value() / 1000.0
        s['line_width'] = self.slider_width.value()
        s['opacity'] = self.slider_opacity.value()
        s['palette'] = self.combo_palette.currentText()
        s['color_source'] = self.combo_color_src.currentText()
        s['show_overlay'] = self.check_overlay.isChecked()
        s['glow'] = self.check_glow.isChecked()
        s['comet'] = self.check_comet.isChecked()
        s['vignette'] = self.check_vignette.isChecked()
        s['bg_style'] = self.combo_bg_style.currentText()
        s['zoom'] = self.slider_zoom.value() / 10.0
        s['pan_x'] = self.slider_pan_x.value() / 100.0
        s['pan_y'] = self.slider_pan_y.value() / 100.0
        
        self.canvas.update_particle_count()
        
        # If background style or color changes, we need to force a full background redraw
        # to avoid leaving trails of the old background.
        if self.sender() in [self.combo_bg_style, self.btn_bg_color]:
            painter = QPainter(self.canvas.buffer)
            self.canvas.draw_background(painter)
            painter.end()

        # Update text labels
        self.lbl_particles.setText(str(s['particle_count']))
        self.lbl_speed.setText(f"{s['speed']:.1f}")
        self.lbl_trail.setText(str(s['trail_fade']))
        self.lbl_time.setText(f"{s['time_scale']:.3f}")
        self.lbl_width.setText(f"{s['line_width']}px")
        self.lbl_opacity.setText(str(s['opacity']))
        self.lbl_zoom.setText(f"{s['zoom']:.1f}x")
        self.lbl_pan_x.setText(f"{s['pan_x']:.2f}")
        self.lbl_pan_y.setText(f"{s['pan_y']:.2f}")

    def pick_bg_color(self):
        color = QColorDialog.getColor(self.canvas.bg_color, self, "Select Background Color")
        if color.isValid():
            self.canvas.bg_color = color
            # Force background redraw
            painter = QPainter(self.canvas.buffer)
            self.canvas.draw_background(painter)
            painter.end()

    def toggle_play(self):
        self.canvas.is_playing = not self.canvas.is_playing
        self.btn_play_pause.setText("▶ Play Preview" if not self.canvas.is_playing else "⏸ Pause Preview")

    def export_png(self):
        filename = "youtube_short_frame.png"
        self.canvas.buffer.save(filename)
        self.status_label.setText(f"Saved PNG frame: {filename}")

    def start_export(self):
        self.btn_export_mp4.setEnabled(False)
        self.btn_export_png.setEnabled(False)
        self.btn_play_pause.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Rendering video... Do not close the application.")

        duration = int(self.combo_duration.currentText().split()[0])
        fps = int(self.combo_fps.currentText().split()[0])

        self.export_thread = ExportThread(self.canvas, duration, fps)
        self.export_thread.progress.connect(self.progress_bar.setValue)
        self.export_thread.finished.connect(self.export_done)
        self.export_thread.start()

    def export_done(self, filename):
        self.status_label.setText(f"✅ Saved video to: {filename}")
        self.btn_export_mp4.setEnabled(True)
        self.btn_export_png.setEnabled(True)
        self.btn_play_pause.setEnabled(True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())