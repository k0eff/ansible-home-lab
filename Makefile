setup_mac:
	brew install hudochenkov/sshpass/sshpass

play:
	ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml

ops:
	# fix for node_exporter
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l ops
diskstorage:
	# fix for node_exporter
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l diskstorage

vpn:
	ansible-playbook -i protected/inventories/inventory-vpn.yaml playbook-vpn.yaml

exo:
	ansible-playbook -i protected/inventories/inventory-exo.yaml playbook-exo.yaml

kubespray-init:
	CONFIG_FILE=kubespray/inventory/cluster00/hosts.yml python3 kubespray/contrib/inventory_builder/inventory.py 192.168.31.202 192.168.31.203 192.168.31.204

read-vars:
	. ./protected/kubespray-settings/vars/vCenter/vars.sh

kubespray-install:
	git clone https://github.com/kubernetes-sigs/kubespray.git

kubespray-deploy-00: read-vars
	cp -rfp protected/kubespray-settings/cluster00 kubespray/inventory && \
	cd kubespray && \
	pwd && \
	ansible-playbook -i inventory/cluster00/hosts.yml --become --become-user=root playbooks/cluster.yml

kubespray-upgrade-00: read-vars
	cp -rfp protected/kubespray-settings/cluster00 kubespray/inventory && \
	cd kubespray && \
	pwd && \
	ansible-playbook -i inventory/cluster00/hosts.yml -e kube_version=v1.30.0  --become --become-user=root -e upgrade_cluster_setup=true playbooks/cluster.yml


galaxy:
	ansible-galaxy install -r ./requirements.yaml
