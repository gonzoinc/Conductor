{# Pin the reviewed SSH-command source bundle in Salt pillar. No MCP or pip.
   SSH users and enrollment-group membership can be managed by local policy or LDAP/SSO. #}
{% set digest = salt['pillar.get']('conductor:ssh_bundle_sha256') %}
{% set mysql = salt['pillar.get']('conductor:mysql_enabled', False) %}
{% set mysql_tcp = salt['pillar.get']('conductor:mysql_tcp', False) %}
{% if not digest or digest|length != 64 %}
conductor-ssh-bundle-hash-required:
  test.fail_without_changes:
    - name: Set conductor:ssh_bundle_sha256 to the reviewed source bundle SHA256.
{% else %}
conductor-ssh-packages:
  pkg.installed:
    - pkgs:
      - python3
      - python3-yaml
      - sudo
{% if mysql %}
      - python3-pymysql
{% endif %}

conductor:
  group.present:
    - system: True
  user.present:
    - system: True
    - gid: conductor
    - home: /var/lib/conductor
    - createhome: False
    - shell: /usr/sbin/nologin
    - require:
      - group: conductor

/etc/conductor:
  file.directory:
    - user: root
    - group: conductor
    - mode: '0750'
    - require:
      - user: conductor

/etc/conductor/conductor.yaml:
  file.managed:
    - source: salt://conductor-ssh/conductor.yaml
    - user: root
    - group: conductor
    - mode: '0640'
    - require:
      - file: /etc/conductor

{% if mysql %}
/etc/conductor/mysql-password:
  file.managed:
    - contents_pillar: conductor:mysql_password
    - user: root
    - group: conductor
    - mode: '0640'
    - require:
      - file: /etc/conductor
{% if mysql_tcp %}

/etc/conductor/mysql-ca.pem:
  file.managed:
    - source: salt://conductor-ssh/mysql-ca.pem
    - user: root
    - group: conductor
    - mode: '0640'
    - require:
      - file: /etc/conductor
{% endif %}
{% endif %}

/usr/local/lib/conductor-installer:
  file.directory:
    - user: root
    - group: root
    - mode: '0755'

/usr/local/lib/conductor-installer/scripts:
  file.directory:
    - user: root
    - group: root
    - mode: '0755'
    - require:
      - file: /usr/local/lib/conductor-installer

{% for name in ['install_ssh_host.py', 'install_host.py'] %}
/usr/local/lib/conductor-installer/scripts/{{ name }}:
  file.managed:
    - source: salt://conductor-ssh/{{ name }}
    - user: root
    - group: root
    - mode: '0644'
    - require:
      - file: /usr/local/lib/conductor-installer/scripts
{% endfor %}

/var/cache/conductor:
  file.directory:
    - user: root
    - group: root
    - mode: '0700'

/var/cache/conductor/conductor-ssh-{{ digest }}.tar.gz:
  file.managed:
    - source: salt://conductor-ssh/conductor-ssh-source.tar.gz
    - source_hash: sha256={{ digest }}
    - user: root
    - group: root
    - mode: '0600'
    - require:
      - file: /var/cache/conductor

conductor-ssh-installed:
  cmd.run:
    - name: python3 /usr/local/lib/conductor-installer/scripts/install_ssh_host.py --config /etc/conductor/conductor.yaml --bundle /var/cache/conductor/conductor-ssh-{{ digest }}.tar.gz --sha256 {{ digest }}
    - unless: python3 /usr/local/lib/conductor-installer/scripts/install_ssh_host.py --check --config /etc/conductor/conductor.yaml --bundle /var/cache/conductor/conductor-ssh-{{ digest }}.tar.gz --sha256 {{ digest }}
    - require:
      - pkg: conductor-ssh-packages
      - file: /etc/conductor/conductor.yaml
      - file: /usr/local/lib/conductor-installer/scripts/install_ssh_host.py
      - file: /usr/local/lib/conductor-installer/scripts/install_host.py
      - file: /var/cache/conductor/conductor-ssh-{{ digest }}.tar.gz
{% if mysql %}
      - file: /etc/conductor/mysql-password
{% if mysql_tcp %}
      - file: /etc/conductor/mysql-ca.pem
{% endif %}
{% endif %}
{% endif %}
