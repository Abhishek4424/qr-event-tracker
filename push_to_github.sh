#!/bin/bash

# Replace YOUR_USERNAME with your actual GitHub username
GITHUB_USERNAME="YOUR_USERNAME"
REPO_NAME="qr-event-tracker"

echo "📦 Pushing code to GitHub..."
echo "Make sure you've created the repository on GitHub first!"
echo ""
echo "Enter your GitHub username (or press Enter to use '$GITHUB_USERNAME'):"
read username
if [ ! -z "$username" ]; then
    GITHUB_USERNAME=$username
fi

# Add remote origin
git remote add origin "https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"

# Push to GitHub
echo "Pushing to https://github.com/$GITHUB_USERNAME/$REPO_NAME.git ..."
git push -u origin main

echo "✅ Code pushed successfully!"
echo ""
echo "🌐 Your repository is now available at:"
echo "   https://github.com/$GITHUB_USERNAME/$REPO_NAME"
echo ""
echo "🚀 Next steps:"
echo "   1. Go to https://render.com"
echo "   2. Sign in and click 'New +' → 'Web Service'"
echo "   3. Connect your GitHub repository"
echo "   4. Deploy will start automatically!"