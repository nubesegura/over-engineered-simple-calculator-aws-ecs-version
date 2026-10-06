terraform {
  required_version = "~> 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67"
    }
    # Ephemeral random_password: the initial password never reaches the state.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}
