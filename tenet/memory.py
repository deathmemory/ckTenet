from tenet.hex import HexController
import zlib

#------------------------------------------------------------------------------
# memory.py -- Memory Dump Controller
#------------------------------------------------------------------------------
#
#    The purpose of this file is to house the 'headless' components of the
#    memory dump window and its underlying functionality. This is split into
#    a model and controller component, of a typical 'MVC' design pattern. 
#
#    As our memory dumps are largely abstracted off a generic 'hex dump',
#    there is very little code that actually has to be applied here (for now)
#

class MemoryController(HexController):
    """
    The Memory Dump Controller (Logic)
    """

    def __init__(self, pctx):
        super(MemoryController, self).__init__(pctx)
        self._title = "Memory View"
        #self.model.hex_format = HexType.MAGIC
        # Dictionary to store injected memory values: {address: (data_bytes, length)}
        self._injected_memory = {}

    def inject_memory(self, address, data_bytes, navigate_to_address=False):
        """
        Inject memory values at the specified address.

        Args:
            address (int): The memory address to inject data at
            data_bytes (bytes or list): The byte data to inject
            navigate_to_address (bool): Whether to navigate the memory view to the injected address
        """
        if not isinstance(data_bytes, (bytes, list, tuple, bytearray)):
            raise TypeError("data_bytes must be bytes, list, tuple, or bytearray")

        if isinstance(data_bytes, (list, tuple)):
            data_bytes = bytes(data_bytes)

        length = len(data_bytes)
        self._injected_memory[address] = (bytes(data_bytes), length)

        # If the injected memory affects the currently displayed region, refresh it
        if hasattr(self, 'model') and self.model:
            display_start = self.model.address
            display_end = self.model.address + self.model.data_size
            inject_start = address
            inject_end = address + length

            # Check if there's overlap between injected memory and displayed memory
            if not (inject_end <= display_start or inject_start >= display_end):
                self.refresh_memory()
            elif navigate_to_address:
                # If no overlap but user wants to navigate, do so
                self.navigate(address)

        elif navigate_to_address:
            # If no model yet but user wants to navigate, do so
            self.navigate(address)

        return True

    def get_injected_memory(self, address, length):
        """
        Get injected memory for the specified address range.

        Args:
            address (int): The starting memory address
            length (int): The number of bytes to retrieve

        Returns:
            bytes or None: The injected memory bytes if found, None otherwise
        """
        for inj_addr, (inj_data, inj_len) in self._injected_memory.items():
            inj_end = inj_addr + inj_len
            req_end = address + length

            # Check if the requested range overlaps with injected memory
            if not (req_end <= inj_addr or address >= inj_end):
                # Calculate the overlapping region
                overlap_start = max(address, inj_addr)
                overlap_end = min(req_end, inj_end)

                if overlap_start < overlap_end:
                    # Extract the overlapping portion
                    inj_offset = overlap_start - inj_addr
                    overlap_size = overlap_end - overlap_start
                    return inj_addr, inj_data[inj_offset:inj_offset + overlap_size]

        return None, None

    def clear_injected_memory(self, address=None):
        """
        Clear injected memory.

        Args:
            address (int, optional): If provided, clear only the memory at this address.
                                   If None, clear all injected memory.
        """
        if address is not None and address in self._injected_memory:
            del self._injected_memory[address]
        else:
            self._injected_memory.clear()

        # Refresh the display after clearing injected memory
        self.refresh_memory()

    def refresh_memory(self):
        """
        Refresh the visible memory, incorporating injected memory values.
        """
        if not self.reader:
            self.model.data = None
            self.model.mask = None
            return

        # Get the base memory from the trace
        memory = self.reader.get_memory(self.model.address, self.model.data_size)

        # Apply injected memory on top of the base memory
        if self._injected_memory:
            display_start = self.model.address
            display_size = self.model.data_size

            # Apply each injected memory region that overlaps with the display region
            for inj_addr, (inj_data, inj_len) in self._injected_memory.items():
                inj_end = inj_addr + inj_len
                req_end = display_start + display_size

                # Check if the injected memory overlaps with the displayed region
                if not (req_end <= inj_addr or display_start >= inj_end):
                    # Calculate the overlapping region
                    overlap_start = max(display_start, inj_addr)
                    overlap_end = min(req_end, inj_end)

                    if overlap_start < overlap_end:
                        # Calculate offsets within the display buffer and injected data
                        disp_offset = overlap_start - display_start
                        inj_offset = overlap_start - inj_addr
                        overlap_size = overlap_end - overlap_start

                        # Copy the injected data into the display buffer
                        for i in range(overlap_size):
                            mem_idx = disp_offset + i
                            inj_idx = inj_offset + i
                            if mem_idx < len(memory.data):
                                memory.data[mem_idx] = inj_data[inj_idx]
                                memory.mask[mem_idx] = 0xFF  # Mark as valid

        self.model.data = memory.data
        self.model.mask = memory.mask
        self.model.delta = self.reader.delta

        if self.view:
            self.view.refresh()

    def load_memory_dumps_from_directory(self, directory_path):
        """
        Load memory dump files from a directory using segments.json configuration and inject them into memory.

        Args:
            directory_path (str): Path to the directory containing memory dump files and segments.json

        Returns:
            bool: True if successful, False otherwise
        """
        import os
        import json
        import zipfile

        # import pydevd_pycharm
        # pydevd_pycharm.settrace('localhost', port=12345, stdoutToServer=True, stderrToServer=True)

        if not os.path.isdir(directory_path):
            print("Error: Directory does not exist: {}".format(directory_path))
            return False

        # Look for segments.json configuration file
        segments_file = os.path.join(directory_path, "segments.json")
        if not os.path.exists(segments_file):
            print("Error: segments.json not found in directory: {}".format(directory_path))
            return False

        # Load the segments configuration
        try:
            with open(segments_file, 'r') as f:
                segments = json.load(f)
        except Exception as e:
            print("Error reading segments.json: {}".format(str(e)))
            return False

        if not isinstance(segments, list):
            print("Error: segments.json should contain an array of segment objects")
            return False

        successful_loads = 0

        # Process each segment in the configuration
        for i, segment in enumerate(segments):
            if not isinstance(segment, dict):
                print("Warning: Segment {} is not a dictionary, skipping".format(i))
                continue

            # Extract required fields
            start_addr = segment.get("start")
            content_file = segment.get("content_file")
            name = segment.get("name", "")

            # Check if this is a debug-related segment (optional check)
            if name and "libhunter.so" in name:
                print("Debug check: found libhunter.so in segment name: {}".format(name))

            if not start_addr or not content_file:
                print("Warning: Segment {} missing 'start' or 'content_file' field, skipping".format(i))
                continue

            # Convert start address to integer if it's not already
            if isinstance(start_addr, str):
                try:
                    start_addr = int(start_addr, 0)  # Auto-detect base (0x for hex, etc.)
                except ValueError:
                    print("Warning: Could not parse start address '{}' in segment {}, skipping".format(start_addr, i))
                    continue

            # Build the full path to the content file
            content_path = os.path.join(directory_path, content_file)

            if not os.path.exists(content_path):
                print("Warning: Content file '{}' not found for segment {}, skipping".format(content_file, i))
                continue

            # Read and decompress the content file
            try:
                data_bytes = None
                data = open(content_path, 'rb').read()
                data_bytes = zlib.decompress(data)

                if data_bytes is None or len(data_bytes) == 0:
                    print("Warning: Empty content file '{}' for segment {}".format(content_file, i))
                    continue

                # Inject the memory at the specified start address
                self.inject_memory(start_addr, data_bytes)
                print("Successfully loaded {} bytes from '{}' at 0x{:X} (segment {})".format(
                    len(data_bytes), content_file, start_addr, i))

                successful_loads += 1

            except Exception as e:
                print("Error processing content file '{}' for segment {}: {}".format(content_file, i, str(e)))
                continue

        if successful_loads > 0:
            print("Successfully loaded {} memory segments from {}".format(successful_loads, directory_path))
            # Refresh the memory view to show the new injected memory
            if hasattr(self, 'model') and self.model:
                self.refresh_memory()
            return True
        else:
            print("No memory segments were successfully loaded from {}".format(directory_path))
            return False
