Чтобы скачать, запустить и т.д.

1.  git clone https://github.com/interlumpen/isvpn.git
    cd isvpn

2.  cp .env.example .env
    nano .env

    В .env вписываем токен бота, все нужные токены и айдишники.

3.  mkdir -p logs
    chmod 777 logs

4.  docker-compose up -d --build bot
    docker-compose logs -f bot

ну и все, пользоваться, меню интуитивно понятное, все такое.