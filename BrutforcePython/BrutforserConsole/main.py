import sys
import time

def ___input():
        while True:
            a = input("Введите 6-значное число: ").strip()
            if a.isdigit() and len(a) == 6:
                digits = a
                if len(digits) == 6:
                    print("Всё верно: 6 цифр")
                    break
                else:
                    print("Ошибка: нужно ввести ровно 6 цифр.")
        start_time = time.time()
        found = False
        return  digits, start_time, a

digits , start_time, a = ___input()

print(f"Запуск брутфорса для поиска числа на 1 потоке: {digits}")

def brutforce():
    for i in range(0, 1000000):
        attempt = str(i).zfill(6)
        elapsed = time.time() - start_time
        if attempt == a:
            print(f"Число найдено: {attempt} (итерация {i})")
            print(f"Пройденное время {elapsed:.3f}")
            found = True
            break
        else:
            print(i)