provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_compute_address" "alphadesk_ip" {
  name   = "alphadesk-ip"
  region = var.region
}

resource "google_compute_disk" "alphadesk_data" {
  name = "alphadesk-data"
  type = "pd-standard"
  zone = var.zone
  size = 20
}

resource "google_compute_instance" "alphadesk" {
  name         = "alphadesk"
  machine_type = "e2-small"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-2204-lts"
      size  = 20
    }
  }

  attached_disk {
    source      = google_compute_disk.alphadesk_data.id
    device_name = "alphadesk-data"
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.alphadesk_ip.address
    }
  }

  tags = ["alphadesk"]

  metadata_startup_script = templatefile("${path.module}/startup.sh", {
    alphadesk_ip   = google_compute_address.alphadesk_ip.address
    admin_username = var.admin_username
    admin_password = var.admin_password
    alpaca_key     = var.alpaca_key
    alpaca_secret  = var.alpaca_secret
    polygon_key    = var.polygon_key
    ds_api_key     = var.ds_api_key
  })

  service_account {
    scopes = ["cloud-platform"]
  }
}

resource "google_compute_firewall" "alphadesk" {
  name    = "alphadesk-dashboard"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["alphadesk"]
}
