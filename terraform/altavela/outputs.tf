output "dashboard_url" {
  value       = "http://${google_compute_address.altavela_ip.address}:8001"
  description = "Altavela dashboard URL"
}

output "ssh_command" {
  value = "gcloud compute ssh ${google_compute_instance.altavela.name} --zone=${var.zone}"
}
