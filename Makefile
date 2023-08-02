setup_mac:
	brew install hudochenkov/sshpass/sshpass

play:
	ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml
