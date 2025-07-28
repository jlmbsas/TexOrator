#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =============================================================================
#                                  TEXORATOR
# =============================================================================
#
# Autor: Jose Luis Mena (con asistencia de IA)
# Versión: 5.2
# Descripción: Una aplicación de Texto-a-Voz (TTS) para Linux que convierte
#              documentos de texto (.pdf, .docx, .odt) y texto simple a audio
#              (.wav, .mp3) usando los motores Piper TTS y Pico TTS. Incluye
#              un editor de texto con corrección ortográfica y gramatical.
#

# --- 1. IMPORTACIÓN DE LIBRERÍAS ---
# Módulos estándar de Python
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog, ttk
import subprocess  # Para ejecutar comandos externos (motores de voz, ffmpeg)
import os          # Para interactuar con el sistema operativo (rutas, archivos)
import re          # Para expresiones regulares (limpieza de texto)
import textwrap    # Para formatear texto de ayuda
import threading   # Para ejecutar tareas pesadas (procesamiento de voz) en segundo plano
import tempfile    # Para crear archivos temporales
import signal      # Para enviar señales a procesos (pausar/reanudar audio)
import webbrowser  # Para abrir enlaces web

# Importaciones opcionales: La aplicación funciona sin ellas, pero con menos características.
# Se comprueba si cada librería está instalada y se establece una bandera (flag).
try: import enchant; ENCHANT_OK = True
except ImportError: ENCHANT_OK = False
try: import language_tool_python; LANGUAGETOOL_OK = True
except ImportError: LANGUAGETOOL_OK = False
try: from PIL import Image, ImageTk; PILLOW_OK = True
except ImportError: PILLOW_OK = False
try: import fitz; FITZ_OK = True  # PyMuPDF
except ImportError: FITZ_OK = False
try: import docx; DOCX_OK = True # python-docx
except ImportError: DOCX_OK = False
try: import ezodf; from ezodf import text as ezodf_text; EZODF_OK = True
except ImportError: EZODF_OK = False

# --- 2. RUTAS Y CONSTANTES ---
# Se definen rutas y valores constantes para mantener el código limpio y fácil de modificar.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Directorio donde se ejecuta el script
APP_ICON_PATH = os.path.join(SCRIPT_DIR, "texorator_ventana.png") # Icono de la ventana
HELP_FILE_PATH = os.path.join(SCRIPT_DIR, "ayuda.txt") # Archivo de texto de la ayuda
PIPER_DIR = os.path.join(SCRIPT_DIR, "piper") # Carpeta que contiene el motor Piper
PIPER_EXECUTABLE = os.path.join(PIPER_DIR, "piper") # Ejecutable de Piper
DEFAULT_PIPER_MODEL = os.path.join(PIPER_DIR, "models", "es_ES-davefx-medium.onnx") # Modelo de voz por defecto
PIPER_SAMPLES_URL = "https://rhasspy.github.io/piper-samples/" # Enlace para descargar más voces

# --- 3. VARIABLES GLOBALES ---
# Variables que necesitan ser accesibles desde diferentes partes del programa.
current_playback_process = None  # Almacena el proceso de reproducción de audio actual (aplay)
app_logo_ref = None              # Referencia para la imagen del logo para evitar que el recolector de basura la elimine
stop_processing_flag = threading.Event() # Bandera para detener el procesamiento de texto si el usuario lo cancela
audio_is_paused = False          # Indica si la reproducción de audio está pausada
TEMP_AUDIO_PATH = "temp_playback.wav" # Archivo temporal donde se guarda el audio generado
spell_checker = None             # Objeto para la corrección ortográfica
semantic_checker = None          # Objeto para la corrección gramatical
piper_model_path = None          # Variable de Tkinter para la ruta del modelo Piper seleccionado

# Sistema de gestión de modelos de Piper
# Diccionario para almacenar los modelos de Piper: {Nombre amigable: ruta_al_archivo.onnx}
piper_models = {}
if os.path.exists(DEFAULT_PIPER_MODEL):
    piper_models["Español - davefx (Default)"] = DEFAULT_PIPER_MODEL

# --- 4. CLASES Y FUNCIONES DE CORRECCIÓN ORTOGRÁFICA Y GRAMATICAL ---

class SpellChecker:
    """Maneja la corrección ortográfica usando la librería PyEnchant."""
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.dictionary = None
        if not ENCHANT_OK: return # Si la librería no está, no hace nada
        try:
            if enchant.dict_exists("es_ES"):
                self.dictionary = enchant.Dict("es_ES")
                self.text_widget.tag_configure("error", foreground="red", underline=True)
        except Exception as e:
            print(f"Error al inicializar Enchant: {e}")

    def check(self):
        """Revisa todo el texto y subraya los errores."""
        if not self.dictionary: return
        self.text_widget.tag_remove("error", "1.0", tk.END) # Limpia errores anteriores
        text = self.text_widget.get("1.0", tk.END)
        # Busca todas las palabras en el texto
        for match in re.finditer(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑüÜ]+\b", text):
            word = match.group(0)
            if not self.dictionary.check(word): # Si la palabra no está en el diccionario
                # La marca como un error
                self.text_widget.tag_add("error", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def get_suggestions(self, word):
        """Devuelve una lista de sugerencias para una palabra."""
        return self.dictionary.suggest(word) if self.dictionary else []

    def add_to_dictionary(self, word):
        """Añade una palabra al diccionario personal del usuario."""
        if self.dictionary:
            self.dictionary.add_to_pwl(word)
            perform_silent_recheck() # Vuelve a revisar para quitar el subrayado

class SemanticChecker:
    """Maneja la corrección gramatical usando language-tool-python."""
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.tool = None
        if not LANGUAGETOOL_OK: return
        self.text_widget.tag_configure("error", foreground="red", underline=True)
        # La inicialización de LanguageTool es lenta, así que se hace en un hilo separado
        # para no congelar la interfaz de usuario.
        threading.Thread(target=self._initialize_tool, daemon=True).start()

    def _initialize_tool(self):
        """Carga el modelo de lenguaje en segundo plano."""
        try:
            self.tool = language_tool_python.LanguageTool('es-ES')
        except Exception as e:
            print(f"Error al inicializar LanguageTool: {e}")
            self.tool = None

    def check(self):
        """Revisa la gramática del texto. Puede ser lento en textos largos."""
        if not self.tool: return
        try:
            matches = self.tool.check(self.text_widget.get("1.0", tk.END))
            for rule in matches:
                # Subraya el error gramatical
                self.text_widget.tag_add("error", f"1.0+{rule.offset}c", f"1.0+{rule.offset + rule.errorLength}c")
        except Exception as e:
            print(f"Error durante la revisión semántica: {e}")

    def get_suggestions_at_offset(self, offset):
        """Obtiene sugerencias para un error en una posición específica del texto."""
        if not self.tool: return None, []
        text = self.text_widget.get("1.0", tk.END)
        matches = self.tool.check(text)
        for rule in matches:
            if rule.offset <= offset < (rule.offset + rule.errorLength):
                return text[rule.offset: rule.offset + rule.errorLength], rule.replacements
        return None, []

# --- Funciones Auxiliares para Corrección ---
def perform_all_checks():
    """Función pública para iniciar una revisión completa (ortografía y gramática)."""
    if not (spell_checker and spell_checker.dictionary) and not (semantic_checker and semantic_checker.tool):
        messagebox.showwarning("Corrector no disponible", "Las librerías 'pyenchant' o 'language-tool-python' no están instaladas.")
        return
    show_progress_bar("Revisando ortografía y gramática...")
    window.update_idletasks() # Actualiza la UI para mostrar la barra de progreso
    perform_silent_recheck()
    hide_progress_bar()
    messagebox.showinfo("Revisión Completa", "Se ha completado la revisión del texto.")

def perform_silent_recheck():
    """Realiza la revisión sin mostrar notificaciones, ideal para después de una corrección."""
    if spell_checker and spell_checker.dictionary:
        spell_checker.check()
    if semantic_checker and semantic_checker.tool:
        semantic_checker.check()

# --- FUNCIONES DE MENÚ CONTEXTUAL (CLICK DERECHO) ---
def show_context_menu(event):
    """Muestra un menú contextual al hacer click derecho."""
    global spell_checker, semantic_checker
    click_index = text_entry.index(f"@{event.x},{event.y}")
    
    # Si el click fue sobre una palabra marcada como "error"
    if "error" in text_entry.tag_names(click_index):
        menu = tk.Menu(text_entry, tearoff=0)
        offset = text_entry.count("1.0", click_index)[0]

        # Primero, busca errores gramaticales en esa posición
        original_semantic, suggestions_semantic = semantic_checker.get_suggestions_at_offset(offset)
        if original_semantic:
            for sugg in suggestions_semantic[:8]: # Muestra hasta 8 sugerencias
                menu.add_command(label=sugg, command=lambda s=sugg: correct_text(click_index, original_semantic, s))
            if suggestions_semantic: menu.add_separator()
        
        # Luego, busca errores ortográficos
        word_start = text_entry.index(f"{click_index} wordstart")
        word_end = text_entry.index(f"{click_index} wordend")
        word = text_entry.get(word_start, word_end)

        if spell_checker and not spell_checker.dictionary.check(word):
            suggestions_spell = spell_checker.get_suggestions(word)
            if suggestions_spell:
                for sugg in suggestions_spell[:8]:
                    menu.add_command(label=sugg, command=lambda s=sugg: correct_text(click_index, word, s))
                menu.add_separator()
            menu.add_command(label=f"Añadir '{word}' al diccionario", command=lambda w=word: add_to_dict_and_recheck(w))
        
        # Si el menú tiene opciones, lo muestra
        if menu.index(tk.END) is not None:
            menu.tk_popup(event.x_root, event.y_root)
        else:
            show_default_menu(event)
    else:
        # Si no hay error, muestra el menú por defecto (Cortar, Copiar, Pegar)
        show_default_menu(event)

def show_default_menu(event):
    """Muestra el menú contextual estándar de edición."""
    default_menu = tk.Menu(text_entry, tearoff=0)
    default_menu.add_command(label="Cortar", command=lambda: text_entry.event_generate("<<Cut>>"))
    default_menu.add_command(label="Copiar", command=lambda: text_entry.event_generate("<<Copy>>"))
    default_menu.add_command(label="Pegar", command=lambda: text_entry.event_generate("<<Paste>>"))
    default_menu.add_separator()
    default_menu.add_command(label="Seleccionar todo", command=lambda: text_entry.tag_add("sel", "1.0", "end"))
    default_menu.tk_popup(event.x_root, event.y_root)

def correct_text(click_index, original, suggestion):
    """Reemplaza la palabra o frase original con la sugerencia seleccionada."""
    word_start = text_entry.search(original, f"{click_index} wordstart", backwards=True)
    if not word_start:
        word_start = text_entry.index(f"{click_index} wordstart")
    word_end = f"{word_start}+{len(original)}c"
    text_entry.delete(word_start, word_end)
    text_entry.insert(word_start, suggestion)
    perform_silent_recheck() # Vuelve a revisar el texto

def add_to_dict_and_recheck(word):
    """Añade una palabra al diccionario y actualiza la revisión."""
    if spell_checker:
        spell_checker.add_to_dictionary(word)

# --- 5. LÓGICA DE PROCESAMIENTO DE VOZ Y ARCHIVOS ---
def clean_text(text):
    """Elimina caracteres no deseados del texto para evitar errores en los motores TTS."""
    return re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑüÜ.,¿?¡! \n]', '', text)

def process_text_to_wav(final_wav_output, on_success, on_failure):
    """Función principal que convierte texto a un archivo WAV."""
    global stop_processing_flag, piper_model_path
    stop_processing_flag.clear()
    if os.path.exists(final_wav_output):
        os.remove(final_wav_output)
    
    raw_text = text_entry.get("1.0", tk.END).strip()
    full_text = clean_text(raw_text)
    if not full_text:
        messagebox.showwarning("Advertencia", "No hay texto para procesar.")
        window.after(0, on_failure)
        return

    selected_engine = engine_var.get()
    try:
        # Lógica para Piper TTS
        if selected_engine == "Piper TTS (Alta Calidad)":
            model_to_use = piper_model_path.get()
            if not model_to_use or not os.path.exists(model_to_use):
                messagebox.showerror("Error de Modelo", "El modelo de Piper seleccionado no es válido o no se encuentra.")
                window.after(0, on_failure)
                return
            # Ejecuta Piper como un subproceso
            process = subprocess.run(
                [PIPER_EXECUTABLE, "--model", model_to_use, "--output_file", final_wav_output],
                input=full_text, text=True, check=True, capture_output=True
            )
        # Lógica para Pico TTS o eSpeak
        else:
            selected_voice = voice_var.get()
            command = ["/usr/bin/pico2wave", "-l", selected_voice, "-w", final_wav_output, full_text] if selected_engine == "Pico TTS (Natural)" else ["/usr/bin/espeak-ng", "-v", selected_voice, "-w", final_wav_output, full_text]
            subprocess.run(command, check=True, capture_output=True, text=True)
        
        # Llama a la función de éxito cuando termina
        window.after(0, on_success, final_wav_output)
    except Exception as e:
        # Muestra un error si algo sale mal
        stderr = e.stderr if hasattr(e, 'stderr') else str(e)
        messagebox.showerror("Error de Procesamiento", f"Ocurrió un error con el motor de voz:\n\n{stderr}")
        window.after(0, on_failure)

def start_processing_thread(on_success, on_failure):
    """Inicia el procesamiento de voz en un hilo separado para no bloquear la GUI."""
    threading.Thread(target=process_text_to_wav, args=(TEMP_AUDIO_PATH, on_success, on_failure), daemon=True).start()

# --- 6. FUNCIONES DE INTERFAZ DE USUARIO (BOTONES Y MENÚS) ---
def speak_text():
    """Inicia todo el proceso de 'Leer Texto'."""
    if not text_entry.get("1.0", tk.END).strip():
        messagebox.showwarning("Advertencia", "No hay texto para leer.")
        return
    update_ui_for_audio_state("processing")
    show_progress_bar("Procesando voz, por favor espere...")
    # Inicia el hilo de procesamiento. Cuando termine, ejecutará una de las dos funciones lambda.
    start_processing_thread(
        lambda wav: (hide_progress_bar(), play_audio()), # En caso de éxito
        lambda: reset_ui_after_action()                 # En caso de fallo
    )

def select_piper_model():
    """Abre un diálogo para que el usuario seleccione un nuevo modelo de voz de Piper."""
    global piper_models
    filepath = filedialog.askopenfilename(
        title="Seleccionar modelo Piper (.onnx)",
        filetypes=[("Modelos ONNX", "*.onnx")]
    )
    if filepath:
        # Piper requiere un archivo .json con el mismo nombre que el .onnx
        json_path = filepath + ".json"
        if os.path.exists(json_path):
            model_name = os.path.basename(filepath).replace(".onnx", "")
            if model_name in piper_models:
                if not messagebox.askyesno("Sobrescribir Modelo", f"El modelo '{model_name}' ya existe.\n¿Desea sobrescribirlo con la nueva ruta?"):
                    return
            
            piper_models[model_name] = filepath # Añade el modelo al diccionario
            update_voice_options() # Actualiza el menú desplegable de voces
            voice_var.set(model_name) # Selecciona el nuevo modelo
            messagebox.showinfo("Modelo Cargado", f"Se ha añadido y seleccionado el modelo:\n{model_name}")
        else:
            messagebox.showwarning("Falta Archivo de Configuración",
                                 f"No se encontró el archivo de configuración requerido:\n{os.path.basename(json_path)}\n\nAsegúrese de que ambos archivos (.onnx y .onnx.json) están en la misma carpeta.")

def update_voice_options(*args):
    """Actualiza la lista de voces en el menú desplegable según el motor TTS seleccionado."""
    global piper_models
    selected_engine = engine_var.get()
    
    menu = voice_menu["menu"]
    menu.delete(0, "end") # Limpia las opciones anteriores
    
    if selected_engine == "Piper TTS (Alta Calidad)":
        voice_menu.config(state=tk.NORMAL if piper_models else tk.DISABLED)
        new_options = list(piper_models.keys())
        if not new_options:
            voice_var.set("(No hay modelos cargados)")
            return
    elif selected_engine == "Pico TTS (Natural)":
        voice_menu.config(state=tk.NORMAL)
        new_options = ["es-ES", "en-US", "en-GB"] # Voces disponibles en Pico
    else: # eSpeak-NG
        voice_menu.config(state=tk.NORMAL)
        new_options = ["es", "es-la", "es+f1", "es+m3"] # Voces de ejemplo para eSpeak

    current_voice = voice_var.get()
    for option in new_options:
        menu.add_command(label=option, command=lambda value=option: voice_var.set(value))

    # Si la voz actual ya no es válida para el nuevo motor, selecciona la primera de la lista
    if current_voice not in new_options:
        voice_var.set(new_options[0])

def update_piper_model_path(*args):
    """Actualiza la variable que almacena la ruta al modelo Piper cada vez que cambia la selección de voz."""
    selected_engine = engine_var.get()
    if selected_engine == "Piper TTS (Alta Calidad)":
        selected_voice_name = voice_var.get()
        path = piper_models.get(selected_voice_name)
        if path:
            piper_model_path.set(path)

def load_file():
    """Carga texto desde un archivo (.pdf, .docx, .odt)."""
    filepath = filedialog.askopenfilename(title="Seleccionar archivo", filetypes=[("Documentos Soportados", "*.pdf *.docx *.odt"), ("Todos los archivos", "*.*")])
    if not filepath: return
    text = ""
    file_type = os.path.splitext(filepath)[1]
    try:
        if file_type == ".pdf" and FITZ_OK:
            with fitz.open(filepath) as doc:
                text = "".join(page.get_text() for page in doc)
        elif file_type == ".docx" and DOCX_OK:
            doc = docx.Document(filepath)
            text = "\n".join([para.text for para in doc.paragraphs])
        elif file_type == ".odt" and EZODF_OK:
            doc = ezodf.opendoc(filepath)
            text = "\n".join(e.plaintext() for e in doc.body if hasattr(e, 'plaintext'))
        else:
            messagebox.showwarning("Formato no soportado", f"Las librerías para '{file_type}' no están instaladas o el formato no es soportado.")
            return
        text_entry.delete("1.0", tk.END)
        text_entry.insert("1.0", text)
        messagebox.showinfo("Éxito", "Archivo cargado correctamente.")
    except Exception as e:
        messagebox.showerror("Error al leer archivo", f"No se pudo leer el archivo:\n{e}")

def save_edition():
    """Guarda el contenido del área de texto en un archivo .odt."""
    if not EZODF_OK:
        messagebox.showerror("Falta Librería", "La función de guardar requiere 'ezodf'. Instálala si es necesario.")
        return
    filepath = filedialog.asksaveasfilename(defaultextension=".odt", filetypes=[("Documento ODT", "*.odt")], title="Guardar edición como ODT")
    if not filepath: return
    text = text_entry.get("1.0", tk.END).strip()
    if not text:
        messagebox.showwarning("Advertencia", "No hay texto para guardar")
        return
    try:
        doc = ezodf.newdoc(doctype='odt', filename=filepath)
        paragraphs = text.split('\n')
        for para in paragraphs:
            doc.body.append(ezodf_text.Paragraph(para))
        doc.save()
        messagebox.showinfo("Éxito", f"Edición guardada en:\n{filepath}")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo guardar el archivo:\n{e}")

def show_help_window():
    """Muestra la ventana de ayuda con el contenido de ayuda.txt."""
    try:
        with open(HELP_FILE_PATH, 'r', encoding='utf-8') as f:
            help_text_content = f.read()
    except Exception as e:
        help_text_content = f"Error al leer 'ayuda.txt':\n{e}"
    
    # Creación de la ventana de ayuda (Toplevel)
    help_win = tk.Toplevel(window)
    help_win.title("Ayuda y Créditos")
    help_win.geometry("550x480")
    help_win.resizable(False, False)
    
    help_display = scrolledtext.ScrolledText(help_win, wrap=tk.WORD, padx=10, pady=10)
    help_display.pack(expand=True, fill='both')
    help_display.insert(tk.END, help_text_content)
    help_display.config(state=tk.DISABLED) # Hacer el texto de solo lectura
    
    tk.Button(help_win, text="Cerrar", command=help_win.destroy).pack(pady=10)
    
    # Hace que la ventana de ayuda sea modal (bloquea la ventana principal)
    help_win.transient(window)
    help_win.grab_set()
    window.wait_window(help_win)


# --- 7. FUNCIONES DE CONTROL DE AUDIO Y ESTADO DE LA UI ---

def play_audio():
    """Inicia o reanuda la reproducción del archivo de audio temporal."""
    global current_playback_process, audio_is_paused
    # Si está en pausa, reanuda el proceso existente
    if audio_is_paused and current_playback_process:
        os.kill(current_playback_process.pid, signal.SIGCONT)
        audio_is_paused = False
        update_ui_for_audio_state("playing")
        check_playback_status()
    # Si no hay nada reproduciéndose, inicia un nuevo proceso
    elif not current_playback_process or current_playback_process.poll() is not None:
        if os.path.exists(TEMP_AUDIO_PATH):
            current_playback_process = subprocess.Popen(["/usr/bin/aplay", TEMP_AUDIO_PATH])
            audio_is_paused = False
            update_ui_for_audio_state("playing")
            check_playback_status()

def pause_audio():
    """Pausa la reproducción de audio actual."""
    global current_playback_process, audio_is_paused
    if current_playback_process and current_playback_process.poll() is None:
        os.kill(current_playback_process.pid, signal.SIGSTOP) # Envía la señal de pausa
        audio_is_paused = True
        update_ui_for_audio_state("paused")

def stop_action():
    """Detiene cualquier acción en curso (procesamiento de voz o reproducción de audio)."""
    global current_playback_process, stop_processing_flag, audio_is_paused
    # Si hay un audio reproduciéndose, lo termina
    if current_playback_process and current_playback_process.poll() is None:
        current_playback_process.terminate()
        current_playback_process = None
    # Si se está procesando texto a voz, activa la bandera para detenerlo
    else:
        stop_processing_flag.set()
    audio_is_paused = False
    update_ui_for_audio_state("stopped")
    hide_progress_bar()

def save_audio():
    """Guarda el audio generado en un archivo WAV o MP3 elegido por el usuario."""
    if not os.path.exists(TEMP_AUDIO_PATH):
        messagebox.showwarning("Sin audio", "Primero genera el audio con 'Leer Texto'.")
        return
    output_path = filedialog.asksaveasfilename(defaultextension=".mp3", filetypes=[("Archivos MP3", "*.mp3"), ("Archivos WAV", "*.wav")], title="Guardar audio")
    if not output_path: return
    
    update_ui_for_audio_state("processing")
    show_progress_bar("Guardando archivo de audio...")
    
    try:
        # Si el usuario elige .mp3, convierte el .wav temporal usando ffmpeg
        if output_path.endswith(".mp3"):
            subprocess.run(["/usr/bin/ffmpeg", "-i", TEMP_AUDIO_PATH, "-q:a", "0", output_path, "-y"], check=True)
        # Si no, simplemente copia el archivo .wav
        else:
            import shutil
            shutil.copy(TEMP_AUDIO_PATH, output_path)
        messagebox.showinfo("Éxito", f"Archivo guardado en:\n{output_path}")
    except Exception as e:
        messagebox.showerror("Error al Guardar", f"No se pudo guardar el archivo:\n{e}\n\nAsegúrate de tener 'ffmpeg' instalado para guardar en formato MP3.")
    finally:
        reset_ui_after_action() # Restaura la interfaz

def clear_text_area():
    """Limpia el cuadro de texto."""
    stop_action()
    text_entry.delete("1.0", tk.END)

def update_ui_for_audio_state(state):
    """Administra el estado (activado/desactivado) de los botones según la acción actual."""
    action_buttons = [read_button, save_button, clear_button, load_button, save_edit_button, check_button, help_button]
    play_buttons = [play_button, pause_button, stop_button]
    
    if state == "processing":
        # Desactiva todo excepto el botón de parar
        for btn in action_buttons + play_buttons: btn.config(state=tk.DISABLED)
        stop_button.config(state=tk.NORMAL)
    elif state == "playing":
        # Desactiva los botones de acción y el de play
        for btn in action_buttons + [play_button]: btn.config(state=tk.DISABLED)
        pause_button.config(state=tk.NORMAL)
        stop_button.config(state=tk.NORMAL)
    elif state == "paused":
        # Desactiva los botones de acción y el de pausa
        for btn in action_buttons + [pause_button]: btn.config(state=tk.DISABLED)
        play_button.config(state=tk.NORMAL)
        stop_button.config(state=tk.NORMAL)
        save_button.config(state=tk.NORMAL)
    else: # Estado "stopped" o "idle" (inactivo)
        for btn in action_buttons: btn.config(state=tk.NORMAL)
        for btn in play_buttons: btn.config(state=tk.DISABLED)
        # Si existe un audio temporal, activa los botones de reproducir y guardar
        if os.path.exists(TEMP_AUDIO_PATH):
            play_button.config(state=tk.NORMAL)
            save_button.config(state=tk.NORMAL)

def check_playback_status():
    """Verifica periódicamente si la reproducción de audio ha terminado."""
    if current_playback_process and current_playback_process.poll() is not None:
        # Si el proceso ha terminado, actualiza la UI
        update_ui_for_audio_state("stopped")
    elif current_playback_process and not audio_is_paused:
        # Si sigue en marcha, vuelve a comprobar en 100 ms
        window.after(100, check_playback_status)

def reset_ui_after_action():
    """Restaura la interfaz a su estado inicial después de una acción."""
    global current_playback_process
    hide_progress_bar()
    current_playback_process = None
    update_ui_for_audio_state("idle")

def on_closing():
    """Función que se ejecuta al cerrar la ventana para limpiar procesos."""
    stop_action()
    window.destroy()

def show_progress_bar(message):
    """Muestra la barra de progreso con un mensaje."""
    progress_label.config(text=message)
    progress_frame.pack(pady=(5,0), fill='x', padx=10)
    progress_bar.start(10)

def hide_progress_bar():
    """Oculta y detiene la barra de progreso."""
    progress_bar.stop()
    progress_frame.pack_forget()

def open_link(url):
    """Abre una URL en el navegador web por defecto."""
    webbrowser.open_new(url)

# --- 8. CONSTRUCCIÓN DE LA INTERFAZ GRÁFICA (GUI) ---
# Creación de la ventana principal
window = tk.Tk()
window.title("TexOrator v5.2")
window.geometry("1400x800")

# Las variables de Tkinter se inicializan DESPUÉS de crear la ventana principal.
piper_model_path = tk.StringVar()

# Carga del icono de la aplicación
try:
    if os.path.exists(APP_ICON_PATH):
        window.iconphoto(True, tk.PhotoImage(file=APP_ICON_PATH))
except tk.TclError:
    print("Advertencia: No se pudo cargar el icono.")

# Asigna la función on_closing al evento de cerrar la ventana
window.protocol("WM_DELETE_WINDOW", on_closing)

# Creación de las pestañas (Notebook)
notebook = ttk.Notebook(window)
notebook.pack(pady=10, padx=10, expand=True, fill='both')
main_tab = ttk.Frame(notebook)
models_tab = ttk.Frame(notebook)
notebook.add(main_tab, text='Principal')
notebook.add(models_tab, text='Gestionar Modelos Piper')

# --- Pestaña Principal ---
# Frame superior con los controles de motor de voz y carga de archivos
top_frame = tk.Frame(main_tab)
top_frame.pack(pady=10, padx=10, fill='x')
left_frame = tk.Frame(top_frame) # Controles de voz
left_frame.pack(side=tk.LEFT, anchor='center')
center_frame = tk.Frame(top_frame) # Logo
center_frame.pack(side=tk.LEFT, expand=True, fill='x', anchor='center')
right_frame = tk.Frame(top_frame) # Botones de archivo y ayuda
right_frame.pack(side=tk.RIGHT, anchor='center')

# Controles de Motor y Voz (izquierda)
tk.Label(left_frame, text="Motor:").pack(side=tk.LEFT, padx=(0,5))
engine_var = tk.StringVar(value="Piper TTS (Alta Calidad)")
engine_menu = tk.OptionMenu(left_frame, engine_var, "Piper TTS (Alta Calidad)", "Pico TTS (Natural)", "eSpeak-NG (Robótica)", command=update_voice_options)
engine_menu.pack(side=tk.LEFT)
tk.Label(left_frame, text="Voz:").pack(side=tk.LEFT, padx=(10,5))
voice_var = tk.StringVar()
voice_menu = tk.OptionMenu(left_frame, voice_var, "")
voice_menu.pack(side=tk.LEFT)
voice_var.trace_add("write", update_piper_model_path) # Llama a la función cuando la voz cambia

# Logo (centro)
if PILLOW_OK:
    try:
        app_logo_ref = ImageTk.PhotoImage(Image.open(APP_ICON_PATH).resize((250, 70), Image.Resampling.LANCZOS))
        tk.Label(center_frame, image=app_logo_ref).pack()
    except Exception as e:
        print(f"Error al cargar logo: {e}")

# Botones de Archivo y Ayuda (derecha)
load_button = tk.Button(right_frame, text="Cargar Archivo...", command=load_file)
load_button.pack(side=tk.LEFT, padx=(0, 5))
save_edit_button = tk.Button(right_frame, text="Guardar Edición", command=save_edition)
save_edit_button.pack(side=tk.LEFT, padx=(0, 5))
check_button = tk.Button(right_frame, text="Revisar Ortografía", command=perform_all_checks)
check_button.pack(side=tk.LEFT, padx=(0, 5))
help_button = tk.Button(right_frame, text="Ayuda", command=show_help_window)
help_button.pack(side=tk.LEFT)

# Área de texto principal
tk.Label(main_tab, text="Escribe o carga un texto aquí:").pack()
text_entry = scrolledtext.ScrolledText(main_tab, wrap=tk.WORD, width=70, height=20, font=("Arial", 11))
text_entry.pack(pady=5, padx=10, expand=True, fill='both')

# Frame para la barra de progreso (inicialmente oculto)
progress_frame = tk.Frame(main_tab)
progress_label = tk.Label(progress_frame, text="Procesando...")
progress_label.pack()
progress_bar = ttk.Progressbar(progress_frame, mode='indeterminate', length=300)
progress_bar.pack(pady=5, fill='x', expand=True)

# Frame para los botones de acción principales
button_frame = tk.Frame(main_tab)
button_frame.pack(pady=10)
BTN_WIDTH_TEXT = 15; BTN_HEIGHT = 2; BTN_WIDTH_ICON = 5; ICON_FONT = ("Arial", 14)
read_button = tk.Button(button_frame, text="Leer Texto", command=speak_text, width=BTN_WIDTH_TEXT, height=BTN_HEIGHT)
read_button.pack(side=tk.LEFT, padx=(5, 20))
play_button = tk.Button(button_frame, text="▶", font=ICON_FONT, command=play_audio, width=BTN_WIDTH_ICON, height=BTN_HEIGHT)
play_button.pack(side=tk.LEFT, padx=2)
pause_button = tk.Button(button_frame, text="⏸", font=ICON_FONT, command=pause_audio, width=BTN_WIDTH_ICON, height=BTN_HEIGHT)
pause_button.pack(side=tk.LEFT, padx=2)
stop_button = tk.Button(button_frame, text="⏹", font=ICON_FONT, command=stop_action, width=BTN_WIDTH_ICON, height=BTN_HEIGHT)
stop_button.pack(side=tk.LEFT, padx=(2, 20))
save_button = tk.Button(button_frame, text="Guardar Audio", command=save_audio, width=BTN_WIDTH_TEXT, height=BTN_HEIGHT)
save_button.pack(side=tk.LEFT, padx=5)
clear_button = tk.Button(button_frame, text="Limpiar", command=clear_text_area, width=BTN_WIDTH_TEXT, height=BTN_HEIGHT)
clear_button.pack(side=tk.LEFT, padx=5)


# --- Pestaña "Gestionar Modelos Piper" ---
models_frame = tk.LabelFrame(models_tab, text="Configuración de Piper TTS", padx=20, pady=20)
models_frame.pack(expand=True, fill='both', padx=20, pady=10)

# Instrucciones para el usuario
instructions_frame = tk.Frame(models_frame)
instructions_frame.pack(fill='x', pady=(0, 20))
tk.Label(instructions_frame, text="Cómo cargar un modelo de voz personalizado:", font=("Arial", 12, "bold")).pack(anchor='w')
link_label = tk.Label(instructions_frame, text=PIPER_SAMPLES_URL, fg="blue", cursor="hand2")
link_label.pack(anchor='w', pady=(5, 10))
link_label.bind("<Button-1>", lambda e: open_link(PIPER_SAMPLES_URL))
instructions_text = textwrap.dedent("""
    1.  Haz clic en el enlace de arriba para ir al catálogo de voces de Piper.
    2.  Elige la voz que quieras. Al pulsar 'Download', se abrirá la página del modelo.
    3.  Descarga tanto el archivo .onnx como el archivo de configuración .onnx.json.
    4.  Coloca ambos archivos descargados juntos en una nueva carpeta.
    5.  Pulsa el botón 'Cargar nuevo modelo' de abajo y selecciona solo el archivo .onnx.
        El archivo de configuración se detectará y cargará automáticamente.
    6.  ¡Listo! El modelo aparecerá como una opción en el menú 'Voz' de la pestaña Principal.
""")
tk.Label(instructions_frame, text=instructions_text, justify=tk.LEFT, wraplength=800).pack(anchor='w')
tk.Button(models_frame, text="Cargar nuevo modelo (.onnx)...", command=select_piper_model).pack(pady=10)
tk.Label(models_frame, text="Ruta del modelo seleccionado actualmente:").pack(pady=(20, 5))
current_model_label = tk.Label(models_frame, textvariable=piper_model_path, wraplength=700, foreground="blue", font=("Arial", 10))
current_model_label.pack()

# --- 9. INICIO DE LA APLICACIÓN ---
if __name__ == "__main__":
    # Inicializa los correctores
    spell_checker = SpellChecker(text_entry)
    semantic_checker = SemanticChecker(text_entry)
    
    # Asocia el menú contextual al click derecho en el área de texto
    text_entry.bind("<Button-3>", show_context_menu)
    
    # Configura el estado inicial de la UI
    update_voice_options()
    update_ui_for_audio_state("idle")
    
    # Limpia el archivo de audio temporal de una sesión anterior, si existe
    if os.path.exists(TEMP_AUDIO_PATH):
        try:
            os.remove(TEMP_AUDIO_PATH)
        except OSError as e:
            print(f"Error al eliminar el archivo temporal: {e}")

    # Inicia el bucle principal de la aplicación
    window.mainloop()
