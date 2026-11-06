"""Live state that survives ``reload_tools``: the one submodule name the reload keeps.

Your project's real connection object or physics handle would live here.
"""

connection = "disconnected"
thresholds = {"grip_force_n": 20.0, "contact_distance_m": 0.01}
