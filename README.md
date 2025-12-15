### ⚙️ Чтобы скачать, запустить и т.д.

1.  **Скачать репозиторий**
```bash
    git clone https://github.com/interlumpen/isvpn.git
    cd isvpn
```

2.  **Вписать все токены**  
```bash
    cp .env.example .env
    nano .env
```
**В .env вписываем токен бота, все нужные токены и айдишники.**

3.  **Создать папку с логами**  
```bash
    mkdir -p logs
    chmod 777 logs
```

4.  **Запустить, смотреть по логам состояние бота**  
```bash
    docker-compose up -d --build bot
    docker-compose logs -f bot
```

### 📱 Ну и все, пользоваться, меню интуитивно понятное, все такое. 