#!/bin/bash
# hook pre-commit — Guardian de las Sombras
# Escanea archivos staged en busca de secretos antes de cada commit.
set -euo pipefail

exec python3 /home/sun/Escritorio/automatizaciones/guardian_sombras/guardian_sombras.py
