#!/bin/bash

# Configure Git SSL certificate
git config --global http.sslCAInfo /etc/ssl/certs/ca-certificates.crt

# Configure Git credentials
git config --global credential.useHttpPath true

# Configure Git to use LF line endings
git config --global core.autocrlf input
git config --global core.eol lf

# Configure how Git handles pull requests
git config pull.rebase false

# Synchronize Python dependencies using uv, copying packages instead of using symbolic links
uv sync --link-mode=copy

# You can add any other setup commands here

# Print a message indicating that the post-start setup is complete
echo "Post-start setup completed successfully"