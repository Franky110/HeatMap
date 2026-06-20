"""Frozen-exe entry point.

PyInstaller bundles this as the single executable. When the exe is re-invoked
with --_combine-trips it runs combine_trips.main(); otherwise it starts the GUI.
"""
import sys

if '--_combine-trips' in sys.argv:
    sys.argv.remove('--_combine-trips')
    import combine_trips
    combine_trips.main()
else:
    import trip_manager
    trip_manager.main()
