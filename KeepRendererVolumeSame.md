Since my CamillaDSP uses a Volume bases loudness I don't want external renderers like airplay to control my volume. To prevent this just execute the folowing patch to comment out the code that that set local volume to 0db
`sudo sed -i '/^# Local$/,/^fi$/ {
  /^# Local$/! s/^/# /
}' /var/local/www/commandw/spspre.sh && \
sudo systemctl restart shairport-sync`
