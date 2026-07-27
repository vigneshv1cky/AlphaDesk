provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_compute_address" "altavela_ip" {
  name   = "altavela-ip"
  region = var.region
}

resource "google_compute_disk" "altavela_data" {
  name = "altavela-data"
  type = "pd-standard"
  zone = var.zone
  size = 20
}

resource "google_compute_instance" "altavela" {
  name         = "altavela"
  machine_type = "e2-small"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-2204-lts"
      size  = 20
    }
  }

  attached_disk {
    source      = google_compute_disk.altavela_data.id
    device_name = "altavela-data"
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.altavela_ip.address
    }
  }

  tags = ["altavela"]

  metadata_startup_script = templatefile("${path.module}/startup.sh", {
    altavela_ip    = google_compute_address.altavela_ip.address
    admin_username = var.admin_username
    admin_password = var.admin_password
    ds_api_key     = var.ds_api_key
  })

  service_account {
    scopes = ["cloud-platform"]
  }
}

resource "google_compute_firewall" "altavela" {
  name    = "altavela-dashboard"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["8001"]
  }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["altavela"]
}
