"""
This file contains some constants you can change to control specific constants used throughout the codebase.
"""

# This is the prefix used for the generated AMI while building.
AMI_NAME = "HoudiniOnAWS-AMI"

# These are the names of the credentials in the secretsmanager on AWS.
# SideFXOAuthCredentials should contain a JSON with `client_id` and `client_secret` keys.
SIDEFX_SECRETS_NAME = "SideFXOAuthCredentials"
# GithubCredentials should contain a JSON with a `PAT` key.
GITHUB_CREDENTIALS_NAME = "GithubCredentials"
