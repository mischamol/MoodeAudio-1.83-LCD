Since my CamillaDSP uses a Volume bases loudness I don't want external renderers like airplay to control my volume. To prevent this just execute the folowing patch to comment out the code that that set local volume to 0db
```
sudo sed -i '/^# Local$/,/^fi$/ {
  /^# Local$/! s/^/# /
}' /var/local/www/commandw/spspre.sh && \
sudo systemctl restart shairport-sync
```

To keep airplay volume at 0 db the following should work
```  
sudo sed -i -E 's|^[[:space:]]*(//[[:space:]]*)?ignore_volume_control[[:space:]]*=.*;|ignore_volume_control = "yes";|' /etc/shairport-sync.conf && \
sudo sed -i -E 's|^[[:space:]]*(//[[:space:]]*)?default_airplay_volume[[:space:]]*=.*;|default_airplay_volume = 0.0;|' /etc/shairport-sync.conf && \
sudo systemctl restart shairport-sync
```
