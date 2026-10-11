#!/bin/sh
# Use the .NET Framework reference API, never Mono's implementation assemblies.
set -eu
cd "$(dirname "$0")"
ref="${MONO_FRAMEWORK_ROOT:-/usr/lib/mono}/4.5-api"
test -f "$ref/mscorlib.dll" || { echo 'Missing .NET Framework 4.5 reference assemblies'; exit 1; }
mcs -noconfig -nostdlib+ -warnaserror+ -target:winexe -out:../OpenFlux.exe \
  -win32manifest:app.manifest -win32icon:icons/main.ico \
  -resource:icons/main.ico,OpenFlux.main.ico -resource:icons/offline.ico,OpenFlux.offline.ico \
  -resource:icons/connecting.ico,OpenFlux.connecting.ico \
  -r:"$ref/mscorlib.dll" -r:"$ref/System.dll" -r:"$ref/System.Core.dll" \
  -r:"$ref/System.Xml.dll" -r:"$ref/System.Windows.Forms.dll" \
  -r:"$ref/System.Drawing.dll" -r:"$ref/System.Runtime.Serialization.dll" OpenFlux.cs
