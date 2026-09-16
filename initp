#!/bin/bash

cat > .gitignore << EOL
.agent
.agents
.claude
.cocoindex_code
.codegraph
.draft
.edits
.plan
.tests
.git
node_modules
.dart_tool/
.flutter-plugins
.flutter-plugins-dependencies
.packages
.pub-cache/
.pub/
build/
*.freezed.dart
*.g.dart
.idea/
*.iml
*.ipr
*.iws
.modules.xml
.vscode/
/android/app/local.properties
/android/key.properties
/android/.gradle/
/android/app/src/main/res/values/google-services.json
/ios/.generated/
/ios/Flutter/Generated.xcconfig
/ios/Flutter/flutter_export_environment.sh
/ios/Pods/
/ios/Runner/GeneratedPluginRegistrant.h
/ios/Runner/GeneratedPluginRegistrant.m
/ios/Runner/GoogleService-Info.plist
*.moved-aside
.xcworkspace/
xcuserdata/
.deploy/
/linux/flutter/ephemeral/
/windows/flutter/ephemeral/
.DS_Store
Thumbs.db
*.log
.env
.env.*
EOL

openspec init
ccc init

# git
git init
git config --global user.email "you@example.com"
git config --global user.name "Your Name"

# copy agents.md
wget -O AGENTS.md https://raw.githubusercontent.com/hoangsoft90/ai_setup/refs/heads/main/AGENTS.md
