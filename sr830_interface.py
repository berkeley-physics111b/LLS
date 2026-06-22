"""
SR830 DSP Lock-In Amplifier - RS232/Serial Interface
=====================================================
Based on the Stanford Research Systems SR830 User's Manual (Rev 2.5, 10/2011).

RS232 Configuration (from manual §5, Remote Programming):
  - The SR830 operates as a DCE device: transmit on pin 3, receive on pin 2
  - Supports CTS/DTR hardware handshaking (CTS=pin 5 output, DTR=pin 20 input)
  - Simple 3-wire mode (pins 2, 3, 7) also supported
  - Word length is always 8 bits; baud rate and parity set via [Setup] key
  - Supported baud rates: 300-19200; parity: Even, Odd, or None
  - Command terminator: <LF> or <CR>; responses terminated by <CR> on RS232
  - 256-character input and output buffers

Important: send OUTX 0 as the very first command to direct responses to RS232.

Usage example:
    from sr830 import SR830
    with SR830('/dev/ttyUSB0') as lia:
        print(lia.identify())
        lia.set_output_interface('rs232')
        lia.set_frequency(1000.0)
        x, y = lia.get_xy()
"""

import serial
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Sensitivity index → value string  (SENS command, manual §5)
# ---------------------------------------------------------------------------
SENSITIVITY_VALUES = {
    0:  '2 nV/fA',    1:  '5 nV/fA',    2:  '10 nV/fA',
    3:  '20 nV/fA',   4:  '50 nV/fA',   5:  '100 nV/fA',
    6:  '200 nV/fA',  7:  '500 nV/fA',  8:  '1 µV/pA',
    9:  '2 µV/pA',    10: '5 µV/pA',    11: '10 µV/pA',
    12: '20 µV/pA',   13: '50 µV/pA',   14: '100 µV/pA',
    15: '200 µV/pA',  16: '500 µV/pA',  17: '1 mV/nA',
    18: '2 mV/nA',    19: '5 mV/nA',    20: '10 mV/nA',
    21: '20 mV/nA',   22: '50 mV/nA',   23: '100 mV/nA',
    24: '200 mV/nA',  25: '500 mV/nA',  26: '1 V/µA',
}

# ---------------------------------------------------------------------------
# Time constant index → value string  (OFLT command, manual §5)
# ---------------------------------------------------------------------------
TIME_CONSTANT_VALUES = {
    0:  '10 µs',   1:  '30 µs',   2:  '100 µs',  3:  '300 µs',
    4:  '1 ms',    5:  '3 ms',    6:  '10 ms',   7:  '30 ms',
    8:  '100 ms',  9:  '300 ms',  10: '1 s',     11: '3 s',
    12: '10 s',    13: '30 s',    14: '100 s',   15: '300 s',
    16: '1 ks',    17: '3 ks',    18: '10 ks',   19: '30 ks',
}

# ---------------------------------------------------------------------------
# Sample rate index → value string  (SRAT command, manual §5)
# ---------------------------------------------------------------------------
SAMPLE_RATE_VALUES = {
    0:  '62.5 mHz', 1:  '125 mHz', 2:  '250 mHz', 3:  '500 mHz',
    4:  '1 Hz',     5:  '2 Hz',    6:  '4 Hz',    7:  '8 Hz',
    8:  '16 Hz',    9:  '32 Hz',   10: '64 Hz',   11: '128 Hz',
    12: '256 Hz',   13: '512 Hz',  14: 'Trigger',
}


class SR830Error(Exception):
    """Raised when the SR830 reports a remote programming error."""


class SR830:
    """
    Serial interface for the Stanford Research Systems SR830 DSP Lock-In Amplifier.

    The SR830 speaks ASCII commands terminated by LF or CR. Responses are
    ASCII strings terminated by CR on RS232. All set/query commands use the
    4-character mnemonic format described in Chapter 5 of the manual.

    The instrument can only output data on one interface at a time (RS232 or
    GPIB). The OUTX 0 command must be the first command sent to direct all
    query responses to RS232.
    """

    MAX_BUFFER_POINTS = 16383   # Maximum data storage buffer size (manual §5)

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = serial.PARITY_NONE,
        timeout: float = 5.0,
        write_timeout: float = 5.0,
        rtscts: bool = False,
        dsrdtr: bool = False,
    ):
        """
        Open the serial port connected to the SR830.

        Parameters
        ----------
        port : str
            Serial port name, e.g. '/dev/ttyUSB0' or 'COM3'.
        baudrate : int
            Must match the baud rate set via the SR830 [Setup] key.
            Supported: 300, 600, 1200, 2400, 4800, 9600, 19200. Default 9600.
        parity : str
            serial.PARITY_NONE, serial.PARITY_EVEN, or serial.PARITY_ODD.
            Must match the parity set via the SR830 [Setup] key.
        timeout : float
            Read timeout in seconds.
        write_timeout : float
            Write timeout in seconds.
        rtscts : bool
            Enable RTS/CTS hardware flow control. The SR830 supports CTS/DTR
            handshaking (CTS=pin 5 output, DTR=pin 20 input). Set True only
            if your cable is wired for it.
        dsrdtr : bool
            Enable DSR/DTR hardware flow control.
        """
        self._port_name = port
        self._serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,   # Always 8 bits on SR830 (manual §5)
            parity=parity,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=write_timeout,
            rtscts=rtscts,
            dsrdtr=dsrdtr,
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        if not self._serial.is_open:
            self._serial.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        """Close the serial port."""
        if self._serial.is_open:
            self._serial.close()

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    def _send(self, command: str):
        """Send a command string, appending a LF terminator."""
        raw = (command.strip() + '\n').encode('ascii')
        self._serial.write(raw)

    def _readline(self) -> str:
        """
        Read one CR-terminated response line from the SR830.
        Returns the decoded string with trailing whitespace stripped.
        Raises SR830Error on timeout.
        """
        line = self._serial.readline()
        if not line:
            raise SR830Error(
                f"Timeout waiting for response from SR830 on {self._port_name}"
            )
        return line.decode('ascii', errors='replace').strip()

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

    def set_output_interface(self, interface: str = 'rs232'):
        """
        OUTX - Direct response output to RS232 or GPIB.

        This must be the first command sent when connecting over RS232.
        The SR830 receives commands on both interfaces but responds only
        on the selected one (manual §5, Setup Commands).

        Parameters
        ----------
        interface : str
            'rs232' (i=0) or 'gpib' (i=1).
        """
        val = 0 if interface.lower() == 'rs232' else 1
        self.write(f'OUTX {val}')

    def get_output_interface(self) -> int:
        """OUTX? - Query the output interface (0=RS232, 1=GPIB)."""
        return self.query_int('OUTX?')

    def reset(self):
        """
        *RST - Reset the SR830 to its default configuration.

        Communications parameters and status registers are NOT changed.
        Any data scan in progress is reset and buffer data is lost.
        Allow ~1 second for completion.
        """
        self.write('*RST')
        time.sleep(1.5)

    def identify(self) -> str:
        """
        *IDN? - Query the device identification string.
        Returns e.g. "Stanford_Research_Systems,SR830,s/n00111,ver1.000".
        """
        return self.query('*IDN?')

    def set_local(self, state: int = 0):
        """
        LOCL - Set the local/remote state.

        Parameters
        ----------
        state : int
            0 = LOCAL  (command execution and keyboard both allowed)
            1 = REMOTE (keyboard/knob locked out except [LOCAL] key)
            2 = LOCAL LOCKOUT (all front panel locked, including [LOCAL])

        Notes
        -----
        The Override Remote mode must be set to No (OVRM 0) for the front
        panel to actually be locked out in REMOTE state (manual §5).
        """
        self.write(f'LOCL {state}')

    def get_local(self) -> int:
        """LOCL? - Query the local/remote state (0=LOCAL, 1=REMOTE, 2=LOCKOUT)."""
        return self.query_int('LOCL?')

    def set_override_remote(self, override: bool):
        """
        OVRM - Set GPIB Override Remote.

        When True, the front panel is NOT locked out in the REMOTE state.
        The SR830 default is Override Remote = Yes.
        """
        self.write(f'OVRM {int(override)}')

    def get_override_remote(self) -> bool:
        """OVRM? - Query Override Remote state."""
        return bool(self.query_int('OVRM?'))

    def set_key_click(self, on: bool):
        """KCLK - Enable (True) or disable (False) key click sound."""
        self.write(f'KCLK {int(on)}')

    def get_key_click(self) -> bool:
        """KCLK? - Query key click state."""
        return bool(self.query_int('KCLK?'))

    def set_alarm(self, on: bool):
        """ALRM - Enable (True) or disable (False) the audible alarm."""
        self.write(f'ALRM {int(on)}')

    def get_alarm(self) -> bool:
        """ALRM? - Query alarm state."""
        return bool(self.query_int('ALRM?'))

    def save_setup(self, buffer: int):
        """
        SSET - Save current setup to setting buffer i (1-9).
        Buffers are retained when the power is turned off.
        """
        if not 1 <= buffer <= 9:
            raise ValueError(f"Buffer must be 1-9, got {buffer}")
        self.write(f'SSET {buffer}')

    def recall_setup(self, buffer: int):
        """
        RSET - Recall setup from setting buffer i (1-9).
        Interface parameters are NOT changed. Raises SR830Error if
        buffer i was never previously saved.
        """
        if not 1 <= buffer <= 9:
            raise ValueError(f"Buffer must be 1-9, got {buffer}")
        self.write(f'RSET {buffer}')

    # ------------------------------------------------------------------
    # Reference and phase
    # ------------------------------------------------------------------

    def set_phase(self, degrees: float):
        """
        PHAS - Set the reference phase shift in degrees.

        Range: -360.00 ≤ x ≤ 729.99, wrapped to ±180°.
        Resolution: 0.01°.
        """
        self.write(f'PHAS {degrees}')

    def get_phase(self) -> float:
        """PHAS? - Query the reference phase shift in degrees."""
        return self.query_float('PHAS?')

    def set_reference_source(self, source: int):
        """
        FMOD - Set the reference source.

        Parameters
        ----------
        source : int
            0 = External, 1 = Internal.
        """
        self.write(f'FMOD {source}')

    def get_reference_source(self) -> int:
        """FMOD? - Query reference source (0=External, 1=Internal)."""
        return self.query_int('FMOD?')

    def set_frequency(self, hz: float):
        """
        FREQ - Set the internal oscillator frequency in Hz.

        Only valid when reference source is Internal (FMOD 1).
        Range: 0.001 ≤ f ≤ 102000 Hz (limited further by harmonic: n×f ≤ 102 kHz).
        Resolution: 5 digits or 0.0001 Hz, whichever is greater.
        """
        self.write(f'FREQ {hz}')

    def get_frequency(self) -> float:
        """FREQ? - Query the reference frequency in Hz (internal or external)."""
        return self.query_float('FREQ?')

    def set_reference_slope(self, slope: int):
        """
        RSLP - Set the external reference trigger slope.

        Parameters
        ----------
        slope : int
            0 = Sine zero crossing, 1 = TTL rising edge, 2 = TTL falling edge.

        Notes
        -----
        At frequencies below 1 Hz, a TTL reference must be used (manual §5).
        """
        self.write(f'RSLP {slope}')

    def get_reference_slope(self) -> int:
        """RSLP? - Query external reference slope (0=Sine, 1=TTL Rise, 2=TTL Fall)."""
        return self.query_int('RSLP?')

    def set_harmonic(self, n: int):
        """
        HARM - Set the detection harmonic.

        Range: 1 ≤ n ≤ 19999, subject to n×f ≤ 102 kHz.
        If n×f > 102 kHz, the SR830 sets n to the largest valid value.
        """
        if not 1 <= n <= 19999:
            raise ValueError(f"Harmonic must be 1-19999, got {n}")
        self.write(f'HARM {n}')

    def get_harmonic(self) -> int:
        """HARM? - Query the detection harmonic."""
        return self.query_int('HARM?')

    def set_sine_amplitude(self, vrms: float):
        """
        SLVL - Set the sine output amplitude in Vrms.

        Range: 0.004 ≤ x ≤ 5.000 Vrms. Resolution: 0.002 V.
        """
        if not 0.004 <= vrms <= 5.000:
            raise ValueError(f"Sine amplitude must be 0.004-5.000 Vrms, got {vrms}")
        self.write(f'SLVL {vrms}')

    def get_sine_amplitude(self) -> float:
        """SLVL? - Query the sine output amplitude in Vrms."""
        return self.query_float('SLVL?')

    # ------------------------------------------------------------------
    # Input and filter
    # ------------------------------------------------------------------

    def set_input_config(self, config: int):
        """
        ISRC - Set the input configuration.

        Parameters
        ----------
        config : int
            0 = A (voltage, single-ended)
            1 = A-B (voltage, differential)
            2 = I (current, 1 MΩ gain)
            3 = I (current, 100 MΩ gain)
        """
        self.write(f'ISRC {config}')

    def get_input_config(self) -> int:
        """ISRC? - Query input configuration (0=A, 1=A-B, 2=I 1MΩ, 3=I 100MΩ)."""
        return self.query_int('ISRC?')

    def set_input_shield(self, grounding: int):
        """
        IGND - Set the input shield grounding.

        Parameters
        ----------
        grounding : int
            0 = Float, 1 = Ground.
        """
        self.write(f'IGND {grounding}')

    def get_input_shield(self) -> int:
        """IGND? - Query input shield grounding (0=Float, 1=Ground)."""
        return self.query_int('IGND?')

    def set_input_coupling(self, coupling: int):
        """
        ICPL - Set the input coupling.

        Parameters
        ----------
        coupling : int
            0 = AC, 1 = DC.
        """
        self.write(f'ICPL {coupling}')

    def get_input_coupling(self) -> int:
        """ICPL? - Query input coupling (0=AC, 1=DC)."""
        return self.query_int('ICPL?')

    def set_line_notch_filter(self, mode: int):
        """
        ILIN - Set the line notch filter(s).

        Parameters
        ----------
        mode : int
            0 = Out (no filters)
            1 = Line notch in (60 Hz / 50 Hz)
            2 = 2×Line notch in (120 Hz / 100 Hz)
            3 = Both notch filters in
        """
        self.write(f'ILIN {mode}')

    def get_line_notch_filter(self) -> int:
        """ILIN? - Query line notch filter mode (0-3)."""
        return self.query_int('ILIN?')

    # ------------------------------------------------------------------
    # Gain and time constant
    # ------------------------------------------------------------------

    def set_sensitivity(self, index: int):
        """
        SENS - Set the sensitivity.

        Parameters
        ----------
        index : int
            0 (2 nV/fA) through 26 (1 V/µA). See SENSITIVITY_VALUES dict.
        """
        if not 0 <= index <= 26:
            raise ValueError(f"Sensitivity index must be 0-26, got {index}")
        self.write(f'SENS {index}')

    def get_sensitivity(self) -> int:
        """SENS? - Query the sensitivity index (0-26)."""
        return self.query_int('SENS?')

    def set_reserve_mode(self, mode: int):
        """
        RMOD - Set the dynamic reserve mode.

        Parameters
        ----------
        mode : int
            0 = High Reserve, 1 = Normal, 2 = Low Noise (minimum reserve).
        """
        self.write(f'RMOD {mode}')

    def get_reserve_mode(self) -> int:
        """RMOD? - Query reserve mode (0=High, 1=Normal, 2=Low Noise)."""
        return self.query_int('RMOD?')

    def set_time_constant(self, index: int):
        """
        OFLT - Set the time constant.

        Parameters
        ----------
        index : int
            0 (10 µs) through 19 (30 ks). See TIME_CONSTANT_VALUES dict.

        Notes
        -----
        Time constants > 30 s may not be set if the detection frequency
        exceeds 200 Hz. Short time constants may be rounded up to the
        minimum allowed value (manual §5).
        """
        if not 0 <= index <= 19:
            raise ValueError(f"Time constant index must be 0-19, got {index}")
        self.write(f'OFLT {index}')

    def get_time_constant(self) -> int:
        """OFLT? - Query the time constant index (0-19)."""
        return self.query_int('OFLT?')

    def set_filter_slope(self, slope: int):
        """
        OFSL - Set the low pass filter slope.

        Parameters
        ----------
        slope : int
            0 = 6 dB/oct, 1 = 12 dB/oct, 2 = 18 dB/oct, 3 = 24 dB/oct.
        """
        self.write(f'OFSL {slope}')

    def get_filter_slope(self) -> int:
        """OFSL? - Query filter slope (0=6, 1=12, 2=18, 3=24 dB/oct)."""
        return self.query_int('OFSL?')

    def set_sync_filter(self, on: bool):
        """
        SYNC - Enable/disable the synchronous filter (below 200 Hz).

        The synchronous filter is active only when the detection frequency
        is less than 200 Hz.
        """
        self.write(f'SYNC {int(on)}')

    def get_sync_filter(self) -> bool:
        """SYNC? - Query synchronous filter state."""
        return bool(self.query_int('SYNC?'))

    # ------------------------------------------------------------------
    # Display and output
    # ------------------------------------------------------------------

    def set_display(self, channel: int, display: int, ratio: int = 0):
        """
        DDEF - Set the CH1 or CH2 display quantity and ratio.

        Parameters
        ----------
        channel : int
            1 = CH1, 2 = CH2.
        display : int
            For CH1: 0=X, 1=R, 2=X Noise, 3=Aux In 1, 4=Aux In 2.
            For CH2: 0=Y, 1=θ, 2=Y Noise, 3=Aux In 3, 4=Aux In 4.
        ratio : int
            For CH1: 0=none, 1=Aux In 1, 2=Aux In 2.
            For CH2: 0=none, 1=Aux In 3, 2=Aux In 4.
        """
        self.write(f'DDEF {channel},{display},{ratio}')

    def get_display(self, channel: int) -> tuple[int, int]:
        """
        DDEF? - Query the display and ratio for channel i.

        Returns
        -------
        (display_index, ratio_index)
        """
        response = self.query(f'DDEF? {channel}')
        parts = response.split(',')
        return int(parts[0]), int(parts[1])

    def set_front_panel_output(self, channel: int, quantity: int):
        """
        FPOP - Set the CH1 or CH2 front panel output source.

        Parameters
        ----------
        channel : int
            1 = CH1, 2 = CH2.
        quantity : int
            For CH1: 0 = CH1 Display, 1 = X.
            For CH2: 0 = CH2 Display, 1 = Y.
        """
        self.write(f'FPOP {channel},{quantity}')

    def get_front_panel_output(self, channel: int) -> int:
        """FPOP? - Query front panel output source for channel i."""
        return self.query_int(f'FPOP? {channel}')

    def set_offset_expand(self, quantity: int, offset_pct: float, expand: int):
        """
        OEXP - Set the output offset and expand.

        Parameters
        ----------
        quantity : int
            1 = X, 2 = Y, 3 = R.
        offset_pct : float
            Offset as a percentage of full scale. -105.00 ≤ x ≤ 105.00.
            Setting offset to 0 turns offset off.
        expand : int
            0 = no expand (×1), 1 = ×10, 2 = ×100.
        """
        self.write(f'OEXP {quantity},{offset_pct},{expand}')

    def get_offset_expand(self, quantity: int) -> tuple[float, int]:
        """
        OEXP? - Query offset and expand for quantity i.

        Returns
        -------
        (offset_percent, expand_index)
            expand_index: 0=×1, 1=×10, 2=×100.
        """
        response = self.query(f'OEXP? {quantity}')
        parts = response.split(',')
        return float(parts[0]), int(parts[1])

    def auto_offset(self, quantity: int):
        """
        AOFF - Auto-offset X, Y or R to zero.

        Parameters
        ----------
        quantity : int
            1 = X, 2 = Y, 3 = R.
        """
        self.write(f'AOFF {quantity}')

    # ------------------------------------------------------------------
    # Aux inputs and outputs
    # ------------------------------------------------------------------

    def get_aux_input(self, input_num: int) -> float:
        """
        OAUX? - Query the voltage of Aux Input i (1-4).

        Range: ±10.5 V. Resolution: 1/3 mV (16-bit ADC).
        Returns the voltage as a float.
        """
        if not 1 <= input_num <= 4:
            raise ValueError(f"Aux input must be 1-4, got {input_num}")
        return self.query_float(f'OAUX? {input_num}')

    def set_aux_output(self, output_num: int, voltage: float):
        """
        AUXV - Set the voltage of Aux Output i (1-4).

        Range: -10.500 ≤ v ≤ 10.500 V. Resolution: 1 mV.
        """
        if not 1 <= output_num <= 4:
            raise ValueError(f"Aux output must be 1-4, got {output_num}")
        if not -10.5 <= voltage <= 10.5:
            raise ValueError(f"Aux output voltage must be ±10.500 V, got {voltage}")
        self.write(f'AUXV {output_num},{voltage}')

    def get_aux_output(self, output_num: int) -> float:
        """AUXV? - Query the voltage of Aux Output i (1-4)."""
        if not 1 <= output_num <= 4:
            raise ValueError(f"Aux output must be 1-4, got {output_num}")
        return self.query_float(f'AUXV? {output_num}')

    # ------------------------------------------------------------------
    # Auto functions
    # ------------------------------------------------------------------

    def auto_gain(self):
        """
        AGAN - Execute Auto Gain (equivalent to [AUTO GAIN] key).

        May take time if the time constant is long. Does nothing if the
        time constant is > 1 second. Use wait_for_ready() to detect
        completion (manual §5).
        """
        self.write('AGAN')

    def auto_reserve(self):
        """
        ARSV - Execute Auto Reserve (equivalent to [AUTO RESERVE] key).

        May take some time. Use wait_for_ready() to detect completion.
        """
        self.write('ARSV')

    def auto_phase(self):
        """
        APHS - Execute Auto Phase (equivalent to [AUTO PHASE] key).

        Outputs take many time constants to reach new values. The phase
        will not change if phase is unstable. Query PHAS? afterwards to
        verify the change (manual §5).
        """
        self.write('APHS')

    # ------------------------------------------------------------------
    # Data storage control
    # ------------------------------------------------------------------

    def set_sample_rate(self, index: int):
        """
        SRAT - Set the data sample rate.

        Parameters
        ----------
        index : int
            0 (62.5 mHz) through 13 (512 Hz) or 14 (Trigger).
            See SAMPLE_RATE_VALUES dict.
        """
        if not 0 <= index <= 14:
            raise ValueError(f"Sample rate index must be 0-14, got {index}")
        self.write(f'SRAT {index}')

    def get_sample_rate(self) -> int:
        """SRAT? - Query the sample rate index (0-14)."""
        return self.query_int('SRAT?')

    def set_end_of_buffer_mode(self, mode: int):
        """
        SEND - Set the end-of-buffer mode.

        Parameters
        ----------
        mode : int
            0 = 1 Shot (stops at end of buffer, alarm sounds).
            1 = Loop (wraps around, keeps most recent 16383 points).

        Notes
        -----
        In Loop mode, pause storage before reading data to avoid
        confusion about which point is the most recent (manual §5).
        """
        self.write(f'SEND {mode}')

    def get_end_of_buffer_mode(self) -> int:
        """SEND? - Query end-of-buffer mode (0=1 Shot, 1=Loop)."""
        return self.query_int('SEND?')

    def set_trigger_start(self, on: bool):
        """TSTR - Enable (True) or disable (False) trigger-starts-scan mode."""
        self.write(f'TSTR {int(on)}')

    def get_trigger_start(self) -> bool:
        """TSTR? - Query trigger-starts-scan state."""
        return bool(self.query_int('TSTR?'))

    def trigger(self):
        """
        TRIG - Software trigger command.
        Equivalent to a trigger at the rear panel Trigger input.
        """
        self.write('TRIG')

    def start_scan(self):
        """STRT - Start or resume data storage. Ignored if storage already running."""
        self.write('STRT')

    def pause_scan(self):
        """PAUS - Pause data storage without resetting the buffer."""
        self.write('PAUS')

    def reset_scan(self):
        """REST - Stop data storage and erase the data buffer."""
        self.write('REST')

    # ------------------------------------------------------------------
    # Data transfer
    # ------------------------------------------------------------------

    def get_output(self, quantity: int) -> float:
        """
        OUTP? - Query the instantaneous value of X, Y, R or θ.

        Parameters
        ----------
        quantity : int
            1 = X (Volts), 2 = Y (Volts), 3 = R (Volts), 4 = θ (degrees).

        Returns
        -------
        float
            The value in Volts (X, Y, R) or degrees (θ).

        Notes
        -----
        For coherent simultaneous readings of X and Y (or R and θ),
        use snap() instead (manual §5).
        """
        if not 1 <= quantity <= 4:
            raise ValueError(f"Quantity must be 1-4 (X, Y, R, θ), got {quantity}")
        return self.query_float(f'OUTP? {quantity}')

    def get_x(self) -> float:
        """Query the X output in Volts."""
        return self.get_output(1)

    def get_y(self) -> float:
        """Query the Y output in Volts."""
        return self.get_output(2)

    def get_r(self) -> float:
        """Query the R (magnitude) output in Volts."""
        return self.get_output(3)

    def get_theta(self) -> float:
        """Query the θ (phase) output in degrees."""
        return self.get_output(4)

    def get_xy(self) -> tuple[float, float]:
        """
        Query X and Y simultaneously using SNAP?.

        Returns
        -------
        (X, Y) both in Volts, recorded at the same instant.
        """
        x, y = self.snap(1, 2)
        return x, y

    def get_r_theta(self) -> tuple[float, float]:
        """
        Query R and θ simultaneously using SNAP?.

        Returns
        -------
        (R in Volts, θ in degrees), recorded at the same instant.
        """
        r, theta = self.snap(3, 4)
        return r, theta

    def get_display_value(self, channel: int) -> float:
        """
        OUTR? - Query the value shown on CH1 or CH2 display.

        Parameters
        ----------
        channel : int
            1 = CH1, 2 = CH2.

        Returns
        -------
        float
            Value in the display units.
        """
        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        return self.query_float(f'OUTR? {channel}')

    def snap(self, *quantities: int) -> list[float]:
        """
        SNAP? - Record 2-6 parameter values simultaneously.

        All values are captured at the same instant, which is critical
        when the time constant is short.

        Parameters
        ----------
        *quantities : int
            2 to 6 values from:
              1=X, 2=Y, 3=R, 4=θ,
              5=Aux In 1, 6=Aux In 2, 7=Aux In 3, 8=Aux In 4,
              9=Reference Frequency, 10=CH1 display, 11=CH2 display.

        Returns
        -------
        list of float
            Values in the same order as requested.

        Example
        -------
        >>> x, y, freq = lia.snap(1, 2, 9)
        """
        if not 2 <= len(quantities) <= 6:
            raise ValueError("SNAP? requires 2-6 parameter indices")
        cmd = 'SNAP? ' + ','.join(str(q) for q in quantities)
        response = self.query(cmd)
        return [float(v) for v in response.split(',')]

    def get_num_stored_points(self) -> int:
        """
        SPTS? - Query the number of points stored in the data buffer.

        Returns N where points are numbered 0 (oldest) to N-1 (most recent).
        Returns 0 if the buffer is reset. Safe to call during active storage.
        """
        return self.query_int('SPTS?')

    def get_trace_ascii(self, channel: int, start_bin: int, count: int) -> list[float]:
        """
        TRCA? - Read stored data points from a channel buffer in ASCII format.

        Parameters
        ----------
        channel : int
            1 = CH1 buffer, 2 = CH2 buffer.
        start_bin : int
            First bin to read (0 = oldest point).
        count : int
            Number of bins to read (≥ 1).

        Returns
        -------
        list of float
            Data values in display units.

        Notes
        -----
        start_bin + count must not exceed SPTS?. In Loop mode, pause
        storage before reading (manual §5).
        """
        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        if count < 1:
            raise ValueError(f"Count must be ≥ 1, got {count}")
        response = self.query(f'TRCA? {channel},{start_bin},{count}')
        # Response is comma-separated values; trailing comma is possible
        return [float(v) for v in response.rstrip(',').split(',') if v.strip()]

    def get_trace_binary_ieee(self, channel: int, start_bin: int, count: int) -> list[float]:
        """
        TRCB? - Read stored data in IEEE 754 binary float format (4 bytes/point).

        Faster than TRCA? for large datasets. The manual notes that binary
        transfer over RS232 is generally not recommended due to timing
        constraints, but is provided here for completeness. Use on RS232 only
        if your serial driver reliably handles raw binary (no CR/LF stripping).

        Parameters
        ----------
        channel : int
            1 = CH1, 2 = CH2.
        start_bin : int
            First bin to read (0 = oldest).
        count : int
            Number of bins to read.

        Returns
        -------
        list of float
            Decoded IEEE 754 float values.
        """
        import struct
        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        self._send(f'TRCB? {channel},{start_bin},{count}')
        num_bytes = count * 4
        raw = self._serial.read(num_bytes)
        if len(raw) != num_bytes:
            raise SR830Error(
                f"TRCB? expected {num_bytes} bytes, got {len(raw)}"
            )
        return list(struct.unpack(f'<{count}f', raw))

    # ------------------------------------------------------------------
    # Status / error reporting
    # ------------------------------------------------------------------

    def clear_status(self):
        """*CLS - Clear all status bytes. Enable registers are NOT cleared."""
        self.write('*CLS')

    def get_serial_poll_byte(self, bit: Optional[int] = None) -> int:
        """
        *STB? - Query the Serial Poll Status Byte (0-255).

        Serial Poll Byte bits (manual §5 Status Byte Definitions):
          bit 0 (SCN) - No scan in progress (stopped or done; paused = in progress)
          bit 1 (IFC) - No command execution in progress (Interface Ready)
          bit 2 (ERR) - Enabled bit in error status byte set
          bit 3 (LIA) - Enabled bit in LIA status byte set
          bit 4 (MAV) - Interface output buffer non-empty
          bit 5 (ESB) - Enabled bit in standard event status byte set
          bit 6 (SRQ) - Service request has occurred
          bit 7       - Unused

        Parameters
        ----------
        bit : int or None
            If given (0-7), query only that bit (returns 0 or 1).

        Notes
        -----
        Reading *STB? does NOT clear the byte. The SCN bit (0) is set when
        storage is stopped or done; a Paused scan is still considered in progress.
        """
        if bit is not None:
            return self.query_int(f'*STB? {bit}')
        return self.query_int('*STB?')

    def get_standard_event_status(self, bit: Optional[int] = None) -> int:
        """
        *ESR? - Query (and clear) the Standard Event Status Byte.

        Bits:
          0 (INP) - Input queue overflow (queues cleared)
          1       - Unused
          2 (QRY) - Output queue overflow
          3       - Unused
          4 (EXE) - Command execution error or parameter out of range
          5 (CMD) - Illegal command received
          6 (URQ) - Key press or knob rotation
          7 (PON) - Power-on event

        Reading the full byte clears it; reading bit i clears only bit i.
        """
        if bit is not None:
            return self.query_int(f'*ESR? {bit}')
        return self.query_int('*ESR?')

    def get_error_status(self, bit: Optional[int] = None) -> int:
        """
        ERRS? - Query (and clear) the Error Status Byte.

        Bits:
          0 - Unused
          1 - Backup Error (battery backup failed)
          2 - RAM Error
          3 - Unused
          4 - ROM Error
          5 - GPIB Error (binary data transfer aborted)
          6 - DSP Error
          7 - Math Error

        Reading the full byte clears it; reading bit i clears only bit i.
        """
        if bit is not None:
            return self.query_int(f'ERRS? {bit}')
        return self.query_int('ERRS?')

    def get_lia_status(self, bit: Optional[int] = None) -> int:
        """
        LIAS? - Query (and clear) the LIA (Lock-In Amplifier) Status Byte.

        Bits:
          0 (RSRV/INPT) - Reserve or input overload
          1 (FILTR)     - Filter overload
          2 (OUTPT)     - Output overload
          3 (UNLK)      - Reference unlock
          4 (RANGE)     - Detection frequency crossed 200 Hz threshold
          5 (TC)        - Time constant changed
          6 (TRIG)      - Unit was triggered
          7             - Unused

        Reading the full byte clears it; reading bit i clears only bit i.
        """
        if bit is not None:
            return self.query_int(f'LIAS? {bit}')
        return self.query_int('LIAS?')

    def set_standard_event_enable(self, value: int, bit: Optional[int] = None):
        """*ESE - Set the Standard Event Status Enable Register."""
        if bit is not None:
            self.write(f'*ESE {bit},{value}')
        else:
            self.write(f'*ESE {value}')

    def set_serial_poll_enable(self, value: int, bit: Optional[int] = None):
        """*SRE - Set the Serial Poll Enable Register."""
        if bit is not None:
            self.write(f'*SRE {bit},{value}')
        else:
            self.write(f'*SRE {value}')

    def set_error_enable(self, value: int, bit: Optional[int] = None):
        """ERRE - Set the Error Status Enable Register."""
        if bit is not None:
            self.write(f'ERRE {bit},{value}')
        else:
            self.write(f'ERRE {value}')

    def set_lia_enable(self, value: int, bit: Optional[int] = None):
        """LIAE - Set the LIA Status Enable Register."""
        if bit is not None:
            self.write(f'LIAE {bit},{value}')
        else:
            self.write(f'LIAE {value}')

    def set_power_on_status_clear(self, on: bool):
        """
        *PSC - Set the Power-On Status Clear bit.

        If True, all status registers and enable registers are cleared at power-up.
        If False, enable registers retain their values, allowing SRQ at power-up.
        """
        self.write(f'*PSC {int(on)}')

    def is_interface_ready(self) -> bool:
        """Return True when the Interface Ready bit (IFC, bit 1) is set."""
        return bool(self.get_serial_poll_byte(1))

    def is_scan_stopped(self) -> bool:
        """Return True when the SCN bit (0) is set (no scan in progress)."""
        return bool(self.get_serial_poll_byte(0))

    def wait_for_ready(self, timeout: float = 30.0, poll_interval: float = 0.1):
        """
        Poll the Interface Ready bit (IFC) until it is set or timeout elapses.

        Raises SR830Error on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_interface_ready():
                return
            time.sleep(poll_interval)
        raise SR830Error("Timed out waiting for SR830 Interface Ready bit")

    def check_errors(self):
        """
        Read status bytes and raise SR830Error if any error bits are set.

        Checks the Standard Event Status Byte and the Error Status Byte.
        Also flags LIA overload conditions.
        """
        ese = self.get_standard_event_status()
        err = self.get_error_status()
        lia = self.get_lia_status()
        messages = []

        if ese & (1 << 4):
            messages.append("Command execution error or out-of-range parameter (ESE bit 4)")
        if ese & (1 << 5):
            messages.append("Illegal command received (ESE bit 5)")
        if ese & (1 << 0):
            messages.append("Input queue overflow (ESE bit 0)")
        if ese & (1 << 2):
            messages.append("Output queue overflow (ESE bit 2)")
        if err & (1 << 7):
            messages.append("Internal math error (ERR bit 7)")
        if err & (1 << 6):
            messages.append("DSP error (ERR bit 6)")
        if err & (1 << 2):
            messages.append("RAM error (ERR bit 2)")
        if lia & (1 << 0):
            messages.append("Reserve or input overload (LIA bit 0)")
        if lia & (1 << 1):
            messages.append("Filter overload (LIA bit 1)")
        if lia & (1 << 2):
            messages.append("Output overload (LIA bit 2)")
        if lia & (1 << 3):
            messages.append("Reference unlock (LIA bit 3)")

        if messages:
            raise SR830Error("; ".join(messages))

    # ------------------------------------------------------------------
    # High-level convenience methods
    # ------------------------------------------------------------------

    def configure_rs232(self):
        """
        Send OUTX 0 to direct all query responses to RS232.
        This should be the very first command sent after opening the port.
        """
        self.set_output_interface('rs232')

    def setup_lockin(
        self,
        frequency_hz: Optional[float] = None,
        sine_amplitude_vrms: Optional[float] = None,
        sensitivity_index: Optional[int] = None,
        time_constant_index: Optional[int] = None,
        filter_slope: int = 1,
        reserve_mode: int = 1,
        input_config: int = 0,
        input_coupling: int = 0,
        reference_source: int = 1,
        phase_degrees: float = 0.0,
    ):
        """
        Convenience: configure the lock-in for a typical measurement.

        Parameters
        ----------
        frequency_hz : float, optional
            Internal reference frequency in Hz (requires reference_source=1).
        sine_amplitude_vrms : float, optional
            Sine output amplitude in Vrms (0.004-5.000).
        sensitivity_index : int, optional
            Sensitivity (0-26). See SENSITIVITY_VALUES.
        time_constant_index : int, optional
            Time constant (0-19). See TIME_CONSTANT_VALUES.
        filter_slope : int
            Low-pass filter slope: 0=6, 1=12 (default), 2=18, 3=24 dB/oct.
        reserve_mode : int
            0=High Reserve, 1=Normal (default), 2=Low Noise.
        input_config : int
            Input configuration: 0=A (default), 1=A-B, 2=I 1MΩ, 3=I 100MΩ.
        input_coupling : int
            0=AC (default), 1=DC.
        reference_source : int
            0=External, 1=Internal (default).
        phase_degrees : float
            Reference phase shift in degrees (default 0.0).
        """
        self.configure_rs232()
        self.set_reference_source(reference_source)
        if frequency_hz is not None:
            self.set_frequency(frequency_hz)
        if sine_amplitude_vrms is not None:
            self.set_sine_amplitude(sine_amplitude_vrms)
        self.set_input_config(input_config)
        self.set_input_coupling(input_coupling)
        if sensitivity_index is not None:
            self.set_sensitivity(sensitivity_index)
        if time_constant_index is not None:
            self.set_time_constant(time_constant_index)
        self.set_filter_slope(filter_slope)
        self.set_reserve_mode(reserve_mode)
        self.set_phase(phase_degrees)

    def read_all(self) -> dict:
        """
        Snapshot all primary lock-in outputs simultaneously.

        Returns
        -------
        dict with keys: 'X', 'Y', 'R', 'theta', 'freq',
                         'aux1', 'aux2', 'aux3', 'aux4'
            All values are floats. X, Y, R in Volts; theta in degrees;
            freq in Hz; aux inputs in Volts.
        """
        # SNAP? captures X, Y, R, θ, AuxIn1-4, Freq in one atomic read
        vals = self.snap(1, 2, 3, 4, 5, 6)          # X Y R θ AuxIn1 AuxIn2
        aux34 = self.snap(7, 8)                       # AuxIn3 AuxIn4
        freq = self.snap(1, 9)[1]                     # freq alongside X
        return {
            'X':     vals[0],
            'Y':     vals[1],
            'R':     vals[2],
            'theta': vals[3],
            'aux1':  vals[4],
            'aux2':  vals[5],
            'aux3':  aux34[0],
            'aux4':  aux34[1],
            'freq':  freq,
        }

    def acquire_buffer(
        self,
        duration_s: float,
        sample_rate_index: int = 9,
        channel: int = 1,
        reset_first: bool = True,
    ) -> list[float]:
        """
        Acquire time-series data into the internal buffer and return it.

        Parameters
        ----------
        duration_s : float
            Approximate acquisition duration in seconds.
        sample_rate_index : int
            SRAT index (0-13). Default 9 = 32 Hz.
        channel : int
            1 = CH1, 2 = CH2.
        reset_first : bool
            If True (default), reset the buffer before starting.

        Returns
        -------
        list of float
            Acquired data points in display units.
        """
        self.set_sample_rate(sample_rate_index)
        self.set_end_of_buffer_mode(0)   # 1 Shot

        if reset_first:
            self.reset_scan()

        self.start_scan()
        time.sleep(duration_s + 0.5)    # Wait for acquisition to finish
        self.pause_scan()               # Freeze the buffer before reading

        n_points = self.get_num_stored_points()
        if n_points == 0:
            return []
        return self.get_trace_ascii(channel, 0, n_points)