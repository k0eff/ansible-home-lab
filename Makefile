setup_mac:
	brew install hudochenkov/sshpass/sshpass

play:
	ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml

ops:
	# fix for node_exporter
	export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
	ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l ops
diskstorage:
	ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l diskstorage

vpn:
	ansible-playbook -i protected/inventories/inventory-vpn.yaml playbook-vpn.yaml

kubespray-init:
	CONFIG_FILE=kubespray/inventory/cluster00/hosts.yml python3 kubespray/contrib/inventory_builder/inventory.py 192.168.31.202 192.168.31.203 192.168.31.204

read-vars:
	. ./protected/kubespray-settings/vars/vCenter/vars.sh

kubespray-deploy-00: read-vars
	cp -rfp protected/kubespray-settings/cluster00 kubespray/inventory && \
	cd kubespray && \
	pwd && \
	ansible-playbook -i inventory/cluster00/hosts.yml --become --become-user=root playbooks/cluster.yml

kubespray-deploy-01-dev: read-vars
	cp -rfp protected/kubespray-settings/cluster01-dev kubespray/inventory && \
	cd kubespray && \
	pwd && \
	ansible-playbook -i inventory/cluster01-dev/hosts.yml --become --become-user=root playbooks/cluster.yml

galaxy:
	ansible-galaxy install -r ./requirements.yaml
