setup_mac:
	brew install hudochenkov/sshpass/sshpass

play:
	ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml

kubespray-init:
	CONFIG_FILE=kubespray/inventory/cluster00/hosts.yml python3 kubespray/contrib/inventory_builder/inventory.py 192.168.31.202 192.168.31.203 192.168.31.204

kubespray-deploy:
	. ./protected/kubespray-settings/vars/vCenter/vars.sh
	cp -rfp protected/kubespray-settings/cluster00 kubespray/inventory && \
	cd kubespray && \
	pwd && \
	ansible-playbook -i inventory/cluster00/hosts.yml --become --become-user=root playbooks/cluster.yml
