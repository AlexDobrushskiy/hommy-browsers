#!/bin/bash
# Sourced by the engine's bash scripts: HB_HOME (this checkout) and every config key (lib/hbconfig.py, config.env).
HB_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export HB_HOME
_hb_cfg=$(/usr/bin/python3 "$HB_HOME/lib/hbconfig.py" shell) || exit 1
eval "$_hb_cfg"
unset _hb_cfg
