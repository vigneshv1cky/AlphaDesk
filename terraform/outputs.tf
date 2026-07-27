output "dashboard_url" {
  value       = "http://${google_compute_address.alphadesk_ip.address}:8000"
  description = "AlphaDesk dashboard URL"
}

output "vm_name" {
  value = google_compute_instance.alphadesk.name
}

output "ssh_command" {
  value = "gcloud compute ssh ${google_compute_instance.alphadesk.name} --zone=${var.zone}"
}
