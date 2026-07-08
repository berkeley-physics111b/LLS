"""
SR760 FFT Spectrum Analyzer - GPIB (PyVISA) Interface
=======================================================
Based on the Stanford Research Systems SR760 User's Manual (Rev 1.9, 03/2018).

GPIB Configuration (from manual §5, Setup Communications):
  - The SR760 listens for commands on GPIB and RS232 simultaneously but
    responds only on the interface selected with OUTP (0=RS232, 1=GPIB).
  - Command terminator: <CR> or <LF> are accepted, but on GPIB the SR760's
    end of a response is marked purely by asserting EOI on the last byte
    ("EOC" in the manual) - it does NOT append a CR/LF character to GPIB
    responses. Because of this, PyVISA must not append its own write
    terminator either, or the extra bytes will be interpreted as a new
    (empty) command. Both write_termination and read_termination are
    therefore set to '' for this instrument; message boundaries are
    handled entirely by the GPIB EOI line.
  - 256-character input and output buffers

Important: Use OUTP 1 at the start of every program to direct responses to GPIB.

Usage example:
    from sr760_interface import SR760
    with SR760(gpib_address=4) as sa:
        print(sa.identify())
        sa.configure_gpib()
        sa.start()
        freqs, amplitudes = sa.get_spectrum(trace=0)
"""

import time
import pyvisa
import matplotlib.pyplot as plt
from typing import Optional


# ---------------------------------------------------------------------------
# Span index → frequency map (SPAN command, manual §5 Frequency Commands)
# ---------------------------------------------------------------------------
SPAN_VALUES = {
    0:  0.191,      # 191 mHz
    1:  0.382,      # 382 mHz
    2:  0.763,      # 763 mHz
    3:  1.5,        # 1.5 Hz
    4:  3.1,        # 3.1 Hz
    5:  6.1,        # 6.1 Hz
    6:  12.2,       # 12.2 Hz
    7:  24.4,       # 24.4 Hz
    8:  48.75,      # 48.75 Hz
    9:  97.5,       # 97.5 Hz
    10: 195.0,      # 195 Hz
    11: 390.0,      # 390 Hz
    12: 780.0,      # 780 Hz
    13: 1560.0,     # 1.56 kHz
    14: 3125.0,     # 3.125 kHz
    15: 6250.0,     # 6.25 kHz
    16: 12500.0,    # 12.5 kHz
    17: 25000.0,    # 25 kHz
    18: 50000.0,    # 50 kHz
    19: 100000.0,   # 100 kHz
}

# Reverse map: Hz → span index (nearest)
SPAN_HZ_TO_IDX = {v: k for k, v in SPAN_VALUES.items()}


class SR760Error(Exception):
    """Raised when the SR760 reports a remote programming error."""


class SR760:
    """
    GPIB (PyVISA) interface for the Stanford Research Systems SR760 FFT
    Spectrum Analyzer.

    The SR760 speaks ASCII commands.  All set/query commands use the
    4-character mnemonic format described in the manual.  On GPIB, the end
    of a response is marked only by the EOI line being asserted on the last
    byte (the manual's "EOC") - the instrument does not append a CR/LF to
    GPIB responses, so both write_termination and read_termination are set
    to '' and message boundaries are left entirely to GPIB EOI handling.
    """

    NUM_BINS = 400          # Normal spectrum: 400 bins (0-399)
    BINS_15OCT = 15         # 15-band octave analysis
    BINS_30OCT = 30         # 30-band octave analysis

    def __init__(
        self,
        resource_name: Optional[str] = None,
        gpib_address: Optional[int] = None,
        board: int = 0,
        timeout: float = 5.0,
        resource_manager: Optional["pyvisa.ResourceManager"] = None,
    ):
        """
        Open a GPIB connection to the SR760 via PyVISA.

        Parameters
        ----------
        resource_name : str, optional
            Full VISA resource string, e.g. 'GPIB0::4::INSTR'. If given,
            gpib_address/board are ignored.
        gpib_address : int, optional
            GPIB primary address (0-30) of the SR760. Used to build
            'GPIB<board>::<gpib_address>::INSTR' when resource_name is not
            supplied. Either resource_name or gpib_address is required.
        board : int
            GPIB interface board/controller index. Default 0.
        timeout : float
            VISA I/O timeout in seconds.
        resource_manager : pyvisa.ResourceManager, optional
            Reuse an existing ResourceManager instead of creating a new one
            (useful when talking to several instruments in one program).
        """
        if resource_name is None:
            if gpib_address is None:
                raise ValueError("Provide either resource_name or gpib_address")
            resource_name = f'GPIB{board}::{gpib_address}::INSTR'
        self._resource_name = resource_name
        self._rm = resource_manager or pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(resource_name)
        self._inst.timeout = timeout * 1000  # PyVISA timeout is in ms

        # The SR760 sees the end of a GPIB message only with EOI (the
        # manual's "EOC") - no CR/LF should be used. Responses terminated
        # with LF. (This is for GPIB.)
        self._inst.write_termination = ''
        self._inst.read_termination = ''

        # Clear queue in case unfinished commands from last session, etc.
        self._inst.clear()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        """Close the GPIB (VISA) session."""
        self._inst.close()

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    def _send(self, command: str):
        """Send a command string over GPIB (EOI marks the end of message)."""
        self._inst.write(command.strip())

    def _readline(self) -> str:
        """
        Read one EOI-terminated response from the SR760.
        Returns the decoded string with surrounding whitespace stripped.
        Raises SR760Error on timeout.
        """
        try:
            line = self._inst.read()
        except pyvisa.errors.VisaIOError as exc:
            raise SR760Error(
                f"Timeout waiting for response from SR760 on {self._resource_name}"
            ) from exc
        if not line:
            raise SR760Error(
                f"Timeout waiting for response from SR760 on {self._resource_name}"
            )
        return line.strip()

    def write(self, command: str):
        """Send a command that expects no response."""
        self._send(command)

    def query(self, command: str) -> str:
        """Send a command and return the response string."""
        self._send(command)
        return self._readline()

    def query_float(self, command: str) -> float:
        """Send a query and return the response as a float."""
        return float(self.query(command))

    def query_int(self, command: str) -> int:
        """Send a query and return the response as an integer."""
        return int(self.query(command))

    # ------------------------------------------------------------------
    # Interface / setup
    # ------------------------------------------------------------------

    def set_output_interface(self, interface: str = 'gpib'):
        """
        OUTP command - direct response output to RS232 or GPIB.

        Parameters
        ----------
        interface : str
            'gpib' (default) or 'rs232'.

        Notes
        -----
        Call this at the start of every program to ensure responses come
        back on GPIB (OUTP 1).  The SR760 receives commands on both
        interfaces simultaneously but responds only on the selected one.
        """
        val = 0 if interface.lower() == 'rs232' else 1
        self.write(f'OUTP {val}')

    def reset(self):
        """
        *RST - Reset the SR760 to its default configuration.
        Communications parameters are NOT changed by this command.
        The command takes some time to complete (~1 s).
        """
        self.write('*RST')
        time.sleep(1.5)

    def identify(self) -> str:
        """
        *IDN? - Query the device identification string.
        Returns a string like "Stanford_Research_Systems,SR760,s/n00001,ver007".
        """
        return self.query('*IDN?')

    def set_local(self, state: int = 0):
        """
        LOCL - Set the local/remote state.

        Parameters
        ----------
        state : int
            0 = LOCAL  (command execution and keyboard both allowed)
            1 = REMOTE (keyboard/knob locked out except [HELP] key)
            2 = LOCAL LOCKOUT (all front panel locked, including [HELP])
        """
        self.write(f'LOCL {state}')

    def get_local(self) -> int:
        """Query the current local/remote state (0=LOCAL, 1=REMOTE, 2=LOCAL LOCKOUT)."""
        return self.query_int('LOCL?')

    # ------------------------------------------------------------------
    # Status / error reporting
    # ------------------------------------------------------------------

    def clear_status(self):
        """*CLS - Clear all status bytes."""
        self.write('*CLS')

    def get_serial_poll_byte(self, bit: Optional[int] = None) -> int:
        """
        *STB? - Query the serial poll status byte (0-255).

        Serial Poll Byte bits (manual §5 Status Byte Definitions):
          bit 0 (SCN)  - No measurements in progress
          bit 1 (IFC)  - No command execution in progress (Interface Ready)
          bit 2 (ERR)  - Unmasked error status bit set
          bit 3 (FFT)  - Unmasked FFT status bit set
          bit 4 (MAV)  - Interface output buffer non-empty
          bit 5 (ESB)  - Unmasked standard status bit set
          bit 6 (SRQ)  - Service request has occurred
          bit 7        - Unused

        Parameters
        ----------
        bit : int or None
            If given (0-7), query only that bit (returns 0 or 1).
        """
        if bit is not None:
            return self.query_int(f'*STB? {bit}')
        return self.query_int('*STB?')

    def get_standard_event_status(self, bit: Optional[int] = None) -> int:
        """
        *ESR? - Query (and clear) the standard event status byte.

        Bits:
          0 (INP)       - Input queue overflow
          1 (LimitFail) - Limit test failure
          2 (QRY)       - Output queue overflow
          3             - Unused
          4 (EXE)       - Command execution error / parameter out of range
          5 (CMD)       - Illegal command received
          6 (URQ)       - Key press or knob rotation
          7 (PON)       - Power-on event
        """
        if bit is not None:
            return self.query_int(f'*ESR? {bit}')
        return self.query_int('*ESR?')

    def get_error_status(self, bit: Optional[int] = None) -> int:
        """
        ERRS? - Query (and clear) the error status byte.

        Bits:
          0 - Print/plot error
          1 - Internal math error
          2 - RAM memory error
          3 - Disk error
          4 - ROM memory error
          5 - A/D error
          6 - DSP error
          7 - Input overload
        """
        if bit is not None:
            return self.query_int(f'ERRS? {bit}')
        return self.query_int('ERRS?')

    def get_fft_status(self, bit: Optional[int] = None) -> int:
        """
        FFTS? - Query (and clear) the FFT/analyzer status byte.

        Bits:
          0 - Triggered (time record triggered)
          1 - Print/plot complete
          2 - New data available for trace 0
          3 - New data available for trace 1
          4 - Linear average complete
          5 - Auto range changed the range
          6 - High voltage detected at input
          7 - Settling complete
        """
        if bit is not None:
            return self.query_int(f'FFTS? {bit}')
        return self.query_int('FFTS?')

    def is_interface_ready(self) -> bool:
        """Return True when the Interface Ready bit (IFC, bit 1) is set."""
        return bool(self.get_serial_poll_byte(1))
    
    def is_average_ready(self) -> bool:
        """Return True when FFT status bit indicates average is complete."""
        return bool(self.get_fft_status(bit=4))

    def wait_for_ready(self, timeout: float = 30.0, poll_interval: float = 0.1):
        """
        Poll the Interface Ready bit until it is set or *timeout* seconds elapse.

        Raises SR760Error on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_interface_ready():
                return
            time.sleep(poll_interval)
        raise SR760Error("Timed out waiting for SR760 Interface Ready bit")
    
    def wait_for_ready_average(self, timeout: float = 30.0, poll_interval: float = 0.25):
        """
        Poll the FFT Average bit until it is set or *timeout* seconds elapse.

        Raises SR760Error on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_average_ready():
                return
            time.sleep(poll_interval)
        raise SR760Error("Timed out waiting for SR760 FFT Average Complete bit")

    def check_errors(self):
        """
        Read the standard event status and error status bytes and raise
        SR760Error if any error bits are set.
        """
        ese = self.get_standard_event_status()
        err = self.get_error_status()
        messages = []
        if ese & (1 << 4):
            messages.append("Command execution error or parameter out of range (ESE bit 4)")
        if ese & (1 << 5):
            messages.append("Illegal command received (ESE bit 5)")
        if ese & (1 << 0):
            messages.append("Input queue overflow (ESE bit 0)")
        if ese & (1 << 2):
            messages.append("Output queue overflow (ESE bit 2)")
        if err & (1 << 7):
            messages.append("Input overload (ERR bit 7)")
        if err & (1 << 0):
            messages.append("Print/plot error (ERR bit 0)")
        if err & (1 << 3):
            messages.append("Disk error (ERR bit 3)")
        if messages:
            raise SR760Error("; ".join(messages))

    # ------------------------------------------------------------------
    # Acquisition control
    # ------------------------------------------------------------------

    def start(self):
        """STRT - Start data acquisition (equivalent to [START] key)."""
        self.write('STRT')

    def pause_continue(self):
        """STCO - Toggle pause/continue (equivalent to [PAUSE CONT] key)."""
        self.write('STCO')

    def auto_range(self, mode: int = 1):
        """
        ARNG - Set ranging mode.

        Parameters
        ----------
        mode : int
            0 = Manual range, 1 = Auto range.
            If mode=1 and auto range is already on, a new auto range is performed.
        """
        self.write(f'ARNG {mode}')

    def get_auto_range(self) -> int:
        """Query ranging mode: 0=Manual, 1=Auto."""
        return self.query_int('ARNG?')

    def auto_scale(self, trace: int = -1):
        """
        AUTS - Auto scale a trace (equivalent to [AUTO SCALE] key).

        Parameters
        ----------
        trace : int
            0=Trace0, 1=Trace1, -1=Active Trace.
        """
        self.write(f'AUTS {trace}')

    # ------------------------------------------------------------------
    # Frequency / span commands
    # ------------------------------------------------------------------

    def set_span(self, span_index: int):
        """
        SPAN - Set the frequency span by index (0-19).

        Use SPAN_VALUES dict to map index to Hz, or call set_span_hz().
        """
        if not 0 <= span_index <= 19:
            raise ValueError(f"span_index must be 0-19, got {span_index}")
        self.write(f'SPAN {span_index}')

    def get_span(self) -> int:
        """SPAN? - Query the span index (0-19)."""
        return self.query_int('SPAN?')

    def get_span_hz(self) -> float:
        """Return the current span in Hz."""
        return SPAN_VALUES[self.get_span()]

    def set_span_hz(self, hz: float):
        """
        Set the span to the closest available value in Hz.

        Raises ValueError if hz does not correspond to a valid span.
        """
        best = min(SPAN_VALUES.values(), key=lambda v: abs(v - hz))
        idx = SPAN_HZ_TO_IDX[best]
        self.set_span(idx)

    def set_start_frequency(self, hz: float):
        """STRF - Set the start frequency in Hz."""
        self.write(f'STRF {hz}')

    def get_start_frequency(self) -> float:
        """STRF? - Query the start frequency in Hz."""
        return self.query_float('STRF?')

    def set_center_frequency(self, hz: float):
        """CTRF - Set the center frequency in Hz."""
        self.write(f'CTRF {hz}')

    def get_center_frequency(self) -> float:
        """CTRF? - Query the center frequency in Hz."""
        return self.query_float('CTRF?')

    # ------------------------------------------------------------------
    # Measurement configuration
    # ------------------------------------------------------------------

    def set_measurement(self, trace: int, meas_type: int):
        """
        MEAS - Set the measurement type for a trace.

        Parameters
        ----------
        trace : int
            0=Trace0, 1=Trace1, -1=Active Trace.
        meas_type : int
            0=Spectrum, 1=PSD, 2=Time Record, 3=Octave Analysis.

        Notes
        -----
        Set measurement first, then display type, then units (manual §5).
        """
        self.write(f'MEAS {trace},{meas_type}')

    def get_measurement(self, trace: int = -1) -> int:
        """MEAS? - Query the measurement type (0-3)."""
        return self.query_int(f'MEAS? {trace}')

    def set_display(self, trace: int, disp_type: int):
        """
        DISP - Set the display type for a trace.

        Parameters
        ----------
        disp_type : int
            0=Log Magnitude, 1=Linear Magnitude, 2=Real, 3=Imaginary, 4=Phase.

        Available display types by measurement:
            Spectrum    → all (0-4)
            PSD         → Log Mag, Lin Mag (0-1)
            Time Record → all (0-4)
            Octave      → Log Mag only (0)
        """
        self.write(f'DISP {trace},{disp_type}')

    def get_display(self, trace: int = -1) -> int:
        """DISP? - Query the display type (0-4)."""
        return self.query_int(f'DISP? {trace}')

    def set_units(self, trace: int, units: int):
        """
        UNIT - Set the display units for a trace.

        Parameters
        ----------
        units : int
            0=Volts Pk (or EU Pk / degrees if phase),
            1=Volts RMS (or EU RMS / radians if phase),
            2=dBV (or dBEU),
            3=dBVrms (or dBEUrms).
        """
        self.write(f'UNIT {trace},{units}')

    def get_units(self, trace: int = -1) -> int:
        """UNIT? - Query display units (0-3)."""
        return self.query_int(f'UNIT? {trace}')

    def set_window(self, trace: int, window: int):
        """
        WNDO - Set the windowing function.

        Parameters
        ----------
        window : int
            0=Uniform, 1=Flattop, 2=Hanning, 3=Blackman-Harris (BMH).

        Notes
        -----
        Both traces use the same window; the 'trace' parameter is required
        by the command but both traces are affected when setting.
        """
        self.write(f'WNDO {trace},{window}')

    def get_window(self, trace: int = -1) -> int:
        """WNDO? - Query the window function index (0-3)."""
        return self.query_int(f'WNDO? {trace}')

    # ------------------------------------------------------------------
    # Input configuration
    # ------------------------------------------------------------------

    def set_input_source(self, source: int):
        """
        ISRC - Set the input source.

        Parameters
        ----------
        source : int
            0 = A (single-ended), 1 = A-B (differential).
        """
        self.write(f'ISRC {source}')

    def get_input_source(self) -> int:
        """ISRC? - Query input source (0=A, 1=A-B)."""
        return self.query_int('ISRC?')

    def set_input_grounding(self, grounding: int):
        """
        IGND - Set input grounding.

        Parameters
        ----------
        grounding : int
            0 = Float, 1 = Ground.
        """
        self.write(f'IGND {grounding}')

    def get_input_grounding(self) -> int:
        """IGND? - Query input grounding (0=Float, 1=Ground)."""
        return self.query_int('IGND?')

    def set_input_coupling(self, coupling: int):
        """
        ICPL - Set input coupling.

        Parameters
        ----------
        coupling : int
            0 = AC, 1 = DC.
        """
        self.write(f'ICPL {coupling}')

    def get_input_coupling(self) -> int:
        """ICPL? - Query input coupling (0=AC, 1=DC)."""
        return self.query_int('ICPL?')

    def set_input_range(self, range_dbv: int):
        """
        IRNG - Set the manual input range in dBV peak.

        Parameters
        ----------
        range_dbv : int
            Input range index.  The SR760 accepts values that correspond to
            the dBV full-scale range shown on the front panel.  Typical values
            range from -50 dBV (most sensitive) to +34 dBV (least sensitive).
            Consult the Setup Input menu on the instrument for the exact list.
        """
        self.write(f'IRNG {range_dbv}')

    def get_input_range(self) -> int:
        """IRNG? - Query the input range index."""
        return self.query_int('IRNG?')

    def trigger_mode(self, mode: int):
        """
        TMOD - Set the trigger mode.

        Parameters
        ----------
        mode : int
            0=Continuous, 1=Internal, 2=External, 3=External TTL.
        """
        self.write(f'TMOD {mode}')

    def get_trigger_mode(self) -> int:
        """TMOD? - Query trigger mode (0-3)."""
        return self.query_int('TMOD?')

    # ------------------------------------------------------------------
    # Averaging
    # ------------------------------------------------------------------

    def set_averaging(self, on: bool):
        """AVGO - Enable (True) or disable (False) averaging."""
        self.write(f'AVGO {int(on)}')

    def get_averaging(self) -> bool:
        """AVGO? - Query averaging on/off state."""
        return bool(self.query_int('AVGO?'))

    def set_num_averages(self, n: int):
        """NAVG - Set the number of averages (2-32000)."""
        if not 2 <= n <= 32000:
            raise ValueError(f"Number of averages must be 2-32000, got {n}")
        self.write(f'NAVG {n}')

    def get_num_averages(self) -> int:
        """NAVG? - Query the number of averages."""
        return self.query_int('NAVG?')

    def set_averaging_type(self, avg_type: int):
        """
        AVGT - Set the averaging type.

        Parameters
        ----------
        avg_type : int
            0=RMS, 1=Vector, 2=Peak Hold.
        """
        self.write(f'AVGT {avg_type}')

    def get_averaging_type(self) -> int:
        """AVGT? - Query averaging type (0=RMS, 1=Vector, 2=Peak Hold)."""
        return self.query_int('AVGT?')

    def set_averaging_mode(self, mode: int):
        """
        AVGM - Set the averaging mode.

        Parameters
        ----------
        mode : int
            0=Linear, 1=Exponential.
        """
        self.write(f'AVGM {mode}')

    def get_averaging_mode(self) -> int:
        """AVGM? - Query averaging mode (0=Linear, 1=Exponential)."""
        return self.query_int('AVGM?')

    def set_overlap(self, percent: float):
        """OVLP - Set the overlap percentage (0.0-100.0)."""
        if not 0.0 <= percent <= 100.0:
            raise ValueError(f"Overlap must be 0-100%, got {percent}")
        self.write(f'OVLP {percent}')

    def get_overlap(self) -> float:
        """OVLP? - Query overlap percentage."""
        return self.query_float('OVLP?')

    # ------------------------------------------------------------------
    # Scale / display
    # ------------------------------------------------------------------

    def set_top_reference(self, trace: int, value: float):
        """TREF - Set the top reference (in display units) for trace."""
        self.write(f'TREF {trace},{value}')

    def get_top_reference(self, trace: int = -1) -> float:
        """TREF? - Query top reference."""
        return self.query_float(f'TREF? {trace}')

    def set_bottom_reference(self, trace: int, value: float):
        """BREF - Set the bottom reference (in display units) for trace."""
        self.write(f'BREF {trace},{value}')

    def get_bottom_reference(self, trace: int = -1) -> float:
        """BREF? - Query bottom reference."""
        return self.query_float(f'BREF? {trace}')

    def set_y_scale(self, trace: int, per_division: float):
        """YDIV - Set the vertical scale (units/division) for trace."""
        self.write(f'YDIV {trace},{per_division}')

    def get_y_scale(self, trace: int = -1) -> float:
        """YDIV? - Query vertical scale."""
        return self.query_float(f'YDIV? {trace}')

    def set_active_trace(self, trace: int):
        """ACTG - Set the active trace (0=Trace0, 1=Trace1)."""
        self.write(f'ACTG {trace}')

    def get_active_trace(self) -> int:
        """ACTG? - Query the active trace number."""
        return self.query_int('ACTG?')

    # ------------------------------------------------------------------
    # Marker commands
    # ------------------------------------------------------------------

    def set_marker(self, trace: int, state: int):
        """
        MRKR - Set marker state for a trace.

        Parameters
        ----------
        state : int
            0=Off, 1=On, 2=Track.
        """
        self.write(f'MRKR {trace},{state}')

    def get_marker(self, trace: int = -1) -> int:
        """MRKR? - Query marker state (0=Off, 1=On, 2=Track)."""
        return self.query_int(f'MRKR? {trace}')

    def move_marker_to_bin(self, trace: int, bin_num: int):
        """MBIN - Move the marker for trace to bin number (0-399)."""
        if not 0 <= bin_num <= 399:
            raise ValueError(f"Bin must be 0-399, got {bin_num}")
        self.write(f'MBIN {trace},{bin_num}')

    def get_marker_x(self, trace: int = -1) -> float:
        """MRKX? - Query the marker X position (frequency or time)."""
        return self.query_float(f'MRKX? {trace}')

    def get_marker_y(self, trace: int = -1) -> float:
        """MRKY? - Query the marker Y position (amplitude in display units)."""
        return self.query_float(f'MRKY? {trace}')

    def marker_to_peak(self):
        """MRPK - Move the marker to the on-screen max or min (equivalent to [MARKER MAX/MIN])."""
        self.write('MRPK')

    def marker_to_center(self):
        """MRCN - Set the center frequency to the marker frequency (equivalent to [MARKER CENTER])."""
        self.write('MRCN')

    def peak_left(self):
        """PKLF - Move the marker to the next peak to the left."""
        self.write('PKLF')

    def peak_right(self):
        """PKRT - Move the marker to the next peak to the right."""
        self.write('PKRT')

    def display_message(self, message: str):
        """
        MSGS - Display a message on the SR760 screen with an audible alarm.

        Parameters
        ----------
        message : str
            Up to 30 characters.  Use underscore (_) for spaces.
            All characters are converted to upper case by the SR760.
        """
        msg = message[:30].replace(' ', '_')
        self.write(f'MSGS {msg}')

    # ------------------------------------------------------------------
    # Data transfer
    # ------------------------------------------------------------------

    def get_bin_value(self, trace: int, bin_num: int) -> float:
        """
        SPEC? - Query a single bin's Y value from trace in ASCII format.

        Parameters
        ----------
        trace : int
            0=Trace0, 1=Trace1, -1=Active Trace.
        bin_num : int
            Bin index 0-399.

        Returns
        -------
        float
            The Y value in display units.
        """
        if not 0 <= bin_num <= 399:
            raise ValueError(f"Bin must be 0-399, got {bin_num}")
        return self.query_float(f'SPEC? {trace},{bin_num}')

    def get_bin_x_value(self, trace: int, bin_num: int) -> float:
        """
        BVAL? - Query a bin's X value (frequency in Hz, or time in seconds).

        Parameters
        ----------
        trace : int
            0, 1, or -1 (active).
        bin_num : int
            0-399.
        """
        if not 0 <= bin_num <= 399:
            raise ValueError(f"Bin must be 0-399, got {bin_num}")
        return self.query_float(f'BVAL? {trace},{bin_num}')

    def get_spectrum(self, trace: int = 0) -> tuple[list[float], list[float]]:
        """
        Transfer the entire spectrum in ASCII format using SPEC?.

        Reads all 400 bins individually (one query per bin).  This is the
        safe method as it uses simple ASCII query/response with no binary
        transfer involved.

        For faster transfer, use get_spectrum_fast() which reads all 400
        comma-separated values from a single SPEC? query.

        Parameters
        ----------
        trace : int
            0=Trace0, 1=Trace1, -1=Active Trace.

        Returns
        -------
        tuple of (frequencies, amplitudes)
            frequencies : list of float - X values in Hz (or seconds for time record)
            amplitudes  : list of float - Y values in display units
        """
        frequencies = []
        amplitudes = []
        for i in range(self.NUM_BINS):
            amplitudes.append(self.query_float(f'SPEC? {trace},{i}'))
            frequencies.append(self.query_float(f'BVAL? {trace},{i}'))
        return frequencies, amplitudes

    def get_spectrum_fast(self, trace: int = 0) -> tuple[list[float], list[float]]:
        """
        Transfer the entire spectrum in a single SPEC? query (ASCII).

        The SR760 returns all 400 data points in one response, comma-separated,
        terminated by CR.  The manual notes that the host should use
        interrupt-driven I/O for this transfer.

        X values (frequencies) are still fetched individually via BVAL? for the
        first and last bin, then interpolated linearly.

        Parameters
        ----------
        trace : int
            0=Trace0, 1=Trace1, -1=Active Trace.

        Returns
        -------
        tuple of (frequencies, amplitudes)
        """
        # Send the all-bins query (omit bin index)
        self._send(f'SPEC? {trace}')
        line = self._readline()
        amplitudes = [float(v) for v in line.split(',')]

        # Build frequency axis from first/last bin X values
        x0 = self.query_float(f'BVAL? {trace},0')
        x_last = self.query_float(f'BVAL? {trace},{len(amplitudes) - 1}')
        n = len(amplitudes)
        if n > 1:
            step = (x_last - x0) / (n - 1)
            frequencies = [x0 + i * step for i in range(n)]
        else:
            frequencies = [x0]

        return frequencies, amplitudes

    # ------------------------------------------------------------------
    # Store / recall
    # ------------------------------------------------------------------

    def set_filename(self, filename: str):
        """FNAM - Set the current filename for store/recall operations (≤8.3 chars)."""
        self.write(f'FNAM {filename}')

    def get_filename(self) -> str:
        """FNAM? - Query the current filename."""
        return self.query('FNAM?')

    def save_trace(self):
        """SVTR - Save the active trace data to the file set by FNAM."""
        self.write('SVTR')

    def save_settings(self):
        """SVST - Save the current settings to the file set by FNAM."""
        self.write('SVST')

    def recall_trace(self):
        """RCTR - Recall trace data from the file set by FNAM."""
        self.write('RCTR')

    def recall_settings(self):
        """RCST - Recall settings from the file set by FNAM."""
        self.write('RCST')

    # ------------------------------------------------------------------
    # High-level convenience methods
    # ------------------------------------------------------------------

    def configure_gpib(self):
        """
        Convenience: send OUTP 1 to direct all responses to GPIB.
        Should be the very first command sent over GPIB.
        """
        self.set_output_interface('gpib')

    def setup_spectrum(
        self,
        span_hz: float,
        center_hz: Optional[float] = None,
        start_hz: Optional[float] = None,
        window: int = 3,
        units: int = 2,
        display: int = 0,
        trace: int = 0,
    ):
        """
        Convenience: configure the analyzer for a basic spectrum measurement.

        Parameters
        ----------
        span_hz : float
            Desired frequency span in Hz (will be rounded to nearest valid span).
        center_hz : float, optional
            Center frequency in Hz.  Mutually exclusive with start_hz.
        start_hz : float, optional
            Start frequency in Hz.  Mutually exclusive with center_hz.
        window : int
            Window function: 0=Uniform, 1=Flattop, 2=Hanning, 3=BMH (default).
        units : int
            Display units: 0=Vpk, 1=Vrms, 2=dBV (default), 3=dBVrms.
        display : int
            Display type: 0=Log Mag (default), 1=Lin Mag, 2=Real, 3=Imag, 4=Phase.
        trace : int
            Which trace to configure: 0 or 1.
        """
        self.configure_gpib()
        self.set_measurement(trace, 0)      # Spectrum
        self.set_display(trace, display)
        self.set_units(trace, units)
        self.set_window(trace, window)
        self.set_span_hz(span_hz)
        if center_hz is not None:
            self.set_center_frequency(center_hz)
        elif start_hz is not None:
            self.set_start_frequency(start_hz)

    def acquire_spectrum(
        self,
        trace: int = 0,
        num_averages: int = 1,
        wait_settle: bool = True,
        settle_timeout: float = 60.0,
    ) -> tuple[list[float], list[float]]:
        """
        Start acquisition, optionally average, and download the spectrum.

        Parameters
        ----------
        trace : int
            Trace to read (0, 1, or -1 for active).
        num_averages : int
            If > 1, enables RMS averaging with this count and waits for completion.
        wait_settle : bool
            If True, poll the Interface Ready bit before downloading data.
        settle_timeout : float
            Seconds to wait for ready/average-complete (default 60 s).

        Returns
        -------
        (frequencies, amplitudes)
        """
        if num_averages > 1:
            self.set_averaging(True)
            self.set_num_averages(num_averages)
            self.set_averaging_type(0)   # RMS
            self.set_averaging_mode(0)   # Linear
        else:
            self.set_averaging(False)

        self.start()

        if wait_settle:
            # Wait until the interface indicates it is done
            if num_averages > 1:
                # For linear averaging, wait for IFC ready (command complete) and
                # FFT status bit 4 (Avg Complete)
                deadline = time.time() + settle_timeout
                while time.time() < deadline:
                    if self.get_fft_status(4):   # Avg Complete bit
                        break
                    time.sleep(0.2)
            else:
                self.wait_for_ready(timeout=settle_timeout)

        return self.get_spectrum_fast(trace)

    def find_peak(self, trace: int = -1) -> tuple[float, float]:
        """
        Move the marker to the peak, then read its X and Y values.

        Returns
        -------
        (frequency_hz, amplitude)
        """
        self.set_marker(trace if trace != -1 else 0, 1)
        self.marker_to_peak()
        freq = self.get_marker_x(trace)
        amp = self.get_marker_y(trace)
        return freq, amp

if __name__ == "__main__":
    with SR760(gpib_address=8) as dev:
        dev.configure_gpib()
        print(dev.identify())
        print("Getting spectrum")
        dev.setup_spectrum(1000, 500)
        freqs, amps = dev.acquire_spectrum()
        plt.plot(freqs, amps)
        plt.xlabel("Frequencies")
        plt.ylabel("Amplitudes")
        plt.show()