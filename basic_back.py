import pandas as pd
import fluidsynth
import time
import threading
import os

# from playsound import playsound

# ----------------------------
# Configuration: Files and settings
# ----------------------------
DATABASE_PATH = os.path.join("Data", "Besura-DB - 3 Raga DB.csv")
EXCEL_PATH = os.path.join("Data", "Besura-DB.xlsx")
SOUNDFONT_DIR = "Data"
HARMONIUM_SF2 = os.path.join(SOUNDFONT_DIR, "harmonium.sf2")
TABLA_SF2 = os.path.join(SOUNDFONT_DIR, "tabla.sf2")
# Tanpura MP3 files are stored under "For male"
TANPURA_MP3_DIR = os.path.join(SOUNDFONT_DIR, "For male")
AUDIO_DRIVER = "dsound"  # Adjust as needed


# ----------------------------
# Utility functions: Dynamic key mapping from Excel
# ----------------------------
def load_key_mapping():
    key_map_df = pd.read_excel(EXCEL_PATH, sheet_name=1)
    return dict(zip(key_map_df['Western'], key_map_df['Midi Key']))


def get_dynamic_sa_mapping(sa_notation: str):
    """
    Given a western notation for Sa (e.g., 'C', 'D#'),
    use Excel mapping to obtain current octave's MIDI value.
    Then dynamically generate lower (current - 12) and higher (current + 12) octave values.
    """
    key_map = load_key_mapping()
    if sa_notation not in key_map:
        raise Exception("Invalid Sa note provided in Excel mapping.")
    current_sa = key_map[sa_notation]
    lower_sa = current_sa - 12
    higher_sa = current_sa + 12
    return lower_sa, current_sa, higher_sa


# ----------------------------
# Raga Conversion and Pattern Generation Functions
# ----------------------------
NOTE_OFFSETS = {
    "S": 0, "r": 1, "R": 2, "g": 3, "G": 4,
    "M": 5, "m": 6, "P": 7, "d": 8, "D": 9,
    "n": 10, "N": 11, "S'": 12
}


def convert_to_midi(note: str, base_sa: int, is_final: bool = False) -> int:
    note = note.strip()
    if note in NOTE_OFFSETS:
        midi_val = base_sa + NOTE_OFFSETS[note]
        if is_final and note == "S":
            midi_val += 12
        return midi_val
    else:
        return base_sa


def split_notes(note_string: str):
    return [n.strip() for n in note_string.split('-') if n.strip()]


def create_swar_mapping(raga_info: dict, base_sa: int):
    aroha = raga_info['Aroha'].split('-')
    avaroha = raga_info['Avaroha'].split('-')
    swar_set = list(dict.fromkeys(aroha + avaroha))
    roles = ['Other'] * len(swar_set)  # Simplified roles
    midi_values = [base_sa + i for i in range(len(swar_set))]
    mapping = {"Swar": swar_set, "MIDI Value": midi_values, "Role": roles}
    return pd.DataFrame(mapping)


def pattern_option_1(swar_df: pd.DataFrame):
    ascending = swar_df['MIDI Value'].tolist() + [swar_df['MIDI Value'].iloc[0]]
    descending = [swar_df['MIDI Value'].iloc[0]] + swar_df['MIDI Value'].tolist()[::-1]
    return ascending + descending


def pattern_option_2(swar_df: pd.DataFrame):
    asc = [val for val in swar_df['MIDI Value'].tolist() for _ in (0, 1)] + \
          [swar_df['MIDI Value'].iloc[0]] * 2
    desc = [swar_df['MIDI Value'].iloc[0]] * 2 + \
           [val for val in swar_df['MIDI Value'].tolist()[::-1] for _ in (0, 1)]
    return asc + desc


def pattern_option_3(swar_df: pd.DataFrame):
    n = len(swar_df)
    asc_groups = []
    for i in range(n - 1):
        group = [swar_df['MIDI Value'].iloc[(i + j) % n] for j in range(4)]
        asc_groups.extend(group + [0])
    desc_groups = []
    for i in range(n - 1):
        group = [swar_df['MIDI Value'].iloc[(i - j) % n] for j in range(4)]
        desc_groups.extend(group + [-1])
    return asc_groups + desc_groups


def pattern_option_4(swar_df: pd.DataFrame):
    n = len(swar_df)
    asc = []
    for i in range(n - 1):
        window = [swar_df['MIDI Value'].iloc[(i + j) % n] for j in range(4)]
        asc.extend(window)
    desc = []
    for i in range(n - 1):
        window = [swar_df['MIDI Value'].iloc[(-i - j) % n] for j in range(4)]
        desc.extend(window)
    return asc + [-1] + desc


def pattern_option_5(swar_df: pd.DataFrame):
    def generate_group(X, Y, Z):
        return [X, Y, X, Y, Z, -1]

    swars = swar_df['MIDI Value'].tolist()
    n = len(swars)
    seg1 = []
    for i in range(4):
        X = swars[i]
        Y = swars[(i + 1) % n]
        Z = swars[(i + 2) % n]
        seg1.extend(generate_group(X, Y, Z))
    seg2 = []
    for i in range(2):
        X = swars[(4 + i) % n]
        Y = swars[(4 + i + 1) % n]
        Z = swars[(4 + i + 2) % n]
        seg2.extend(generate_group(X, Y, Z))
    seg3 = []
    for i in range(6):
        X = swars[(-i) % n]
        Y = swars[(-i - 1) % n]
        Z = swars[(-i - 2) % n]
        seg3.extend(generate_group(X, Y, Z))
    return seg1 + [-99] + seg2 + [-99] + seg3


pattern_functions = {
    "Aroha & Avroha": pattern_option_1,
    "SA SA RE RE GA GA": pattern_option_2,
    "SA RE GA - RE GA MA": pattern_option_3,
    "SA RE GA MA RE GA MA PA": pattern_option_4,
    "SA RE SA RE GA": pattern_option_5
}


def build_dynamic_playback_sequence(base_sequence):
    higher = [n + 12 if n >= 0 else n for n in base_sequence]
    lower = [n - 12 if n >= 0 else n for n in base_sequence]
    return base_sequence + higher + lower


# ----------------------------
# Audio Playback Functions
# ----------------------------
def play_sequence_dynamic(synth, sequence, tempo, current_sa):
    """
    Plays the sequence using FluidSynth. For each note, it prints which note is playing,
    along with an octave label:
      - If the MIDI note is less than current_sa, it's lower octave (S1).
      - If it is between current_sa and current_sa+12, it's current octave (S2).
      - Otherwise, it's higher octave (S3).
    """
    note_duration = 60 / tempo
    for midi_note in sequence:
        if midi_note < 0:  # Separator/silence indicator
            time.sleep(note_duration)
        else:
            if midi_note < current_sa:
                octave_label = "S1"
            elif midi_note < current_sa + 12:
                octave_label = "S2"
            else:
                octave_label = "S3"
            print(f"Playing MIDI note {midi_note} ({octave_label})")
            synth.noteon(0, midi_note, 100)
            time.sleep(note_duration)
            synth.noteoff(0, midi_note)


def play_raga_sequence(midi_sequence, instrument: int, tempo: int = 60, current_sa: int = 0):
    fs = fluidsynth.Synth()
    fs.start(driver=AUDIO_DRIVER)
    sfid = fs.sfload(HARMONIUM_SF2)
    fs.program_select(0, sfid, instrument, 0)
    play_sequence_dynamic(fs, midi_sequence, tempo, current_sa)
    fs.delete()


''''def play_taal(tempo: int = 60):
    fs = fluidsynth.Synth()
    fs.start(driver=AUDIO_DRIVER)
    sfid = fs.sfload(TABLA_SF2)
    fs.program_select(0, sfid, 0, 0)
    beat_duration = 60 / tempo
    teentaal = [36, 38, 36, 39] * 4
    while True:
        for beat in teentaal:
            fs.noteon(0, beat, 100)
            time.sleep(beat_duration * 0.5)
            fs.noteoff(0, beat)

def play_tanpura_mp3(selected_sa: str, tempo: int = 60):
    """
    Play the tanpura drone using an MP3 file based on the selected Sa.
    For example, if selected_sa is "C#", it will look for:
        soundfont/For male/C#.mp3
    This function loops continuously.
    """
    mp3_file = os.path.join(TANPURA_MP3_DIR, f"{selected_sa}.mp3")
    if not os.path.exists(mp3_file):
        print(f"Tanpura MP3 file not found: {mp3_file}")
        return
    loop_delay = 10  # seconds, adjust as necessary
    while True:
        try:
            playsound(mp3_file)
        except Exception as e:
            print(f"Error playing {mp3_file}: {e}")
        time.sleep(0.5)

def start_accompaniment(selected_sa: str, tempo: int = 40):
    threading.Thread(target=play_tanpura_mp3, args=(selected_sa, tempo), daemon=True).start()
    threading.Thread(target=play_taal, args=(tempo,), daemon=True).start()'''


# ----------------------------
# Main Routine (Interactive)
# ----------------------------
def main():
    print("Welcome to the Dynamic Riyaz Player!")

    # Get user inputs interactively
    raga_name = input("Enter Raga Name (as in database): ").strip()
    sa = input("Enter Base Sa (e.g., C, C#, D): ").strip()
    pattern_option = input("Enter Pattern Option (e.g., 'Aroha & Avroha'): ").strip()
    tempo = int(input("Enter Tempo in BPM (e.g., 120): ").strip())

    # Load the raga database and raga info Excel sheet
    try:
        df = pd.read_csv(DATABASE_PATH)
        raga_db_df = pd.read_excel(EXCEL_PATH, sheet_name=3)
    except Exception as e:
        print(f"Error loading database or Excel file: {e}")
        return

    # Get key mapping for Sa
    try:
        key_map = load_key_mapping()
        if sa not in key_map:
            print("Invalid Sa note provided. Please check the Excel key mapping.")
            return
        user_sa = key_map[sa]
    except Exception as e:
        print(e)
        return

    # Validate raga name
    print(df.columns)

    if raga_name not in df["Raga"].values:
        print("Raga not found in the database.")
        return
    raga_row = df.loc[df["Raga"] == raga_name].iloc[0]

    # Get raga info from Excel sheet (sheet 3)
    try:
        raga_info = raga_db_df[raga_db_df['Raga'] == raga_name].iloc[0].to_dict()
    except Exception as e:
        print(f"Raga not found in Excel: {raga_name}")
        return

    # Create swar mapping and generate base pattern
    swar_mapping_df = create_swar_mapping(raga_info, user_sa)
    if pattern_option not in pattern_functions:
        print("Invalid pattern option provided.")
        return
    base_sequence = pattern_functions[pattern_option](swar_mapping_df)
    dynamic_sequence = build_dynamic_playback_sequence(base_sequence)

    '''# Start accompaniment (tanpura and taal)
    # Extract the Sa letter (if user input includes octave numbers, strip them)
    sa_letter = sa.rstrip("0123456789")
    threading.Thread(target=start_accompaniment, args=(sa_letter, tempo), daemon=True).start()'''

    # Start raga playback
    # Here we pass the current Sa (user_sa) to determine octave labels in playback
    threading.Thread(target=play_raga_sequence, args=(dynamic_sequence, 0, tempo, user_sa), daemon=True).start()
    time.sleep(0.5)

    print(f"Playing {raga_name} with Sa {sa} at {tempo} BPM...")
    # Keep the script running
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
