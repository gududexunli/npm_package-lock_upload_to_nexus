import sys
import os

# 确保脚本可以找到其他 .py 文件
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from download_packages import main_download
    from upload_to_nexus import main_upload
except ImportError as e:
    print(f"错误: 无法导入依赖的脚本。请确保 main.py, download_packages.py, 和 upload_to_nexus.py 在同一目录下。")
    print(f"详细错误: {e}")
    sys.exit(1)


def print_menu():
    """打印用户菜单"""
    print("\n==============================")
    print("  NPM Nexus 迁移工具")
    print("==============================")
    print("请选择要执行的操作:")
    print("\n  1. 下载 npm 包")
    print("     (根据 package-lock.json 下载 .tgz 并生成 meta-info.json)")
    print("\n  2. 上传 npm 包到 Nexus")
    print("     (根据 meta-info.json 检查并上传到 Nexus)")
    print("\n  Q. 退出")
    print("------------------------------")


def main():
    """主循环"""
    while True:
        print_menu()
        choice = input("请输入选项 (1, 2, 或 Q): ").strip().upper()

        if choice == '1':
            print("\n*** 开始执行 [1. 下载 npm 包] ***\n")
            try:
                main_download()
            except Exception as e:
                print(f"\n!! 下载过程中发生未捕获的错误: {e}")
            print("\n*** [1. 下载] 执行完毕 ***")

        elif choice == '2':
            print("\n*** 开始执行 [2. 上传 npm 包到 Nexus] ***\n")
            try:
                main_upload()
            except Exception as e:
                print(f"\n!! 上传过程中发生未捕获的错误: {e}")
            print("\n*** [2. 上传] 执行完毕 ***")

        elif choice == 'Q':
            print("正在退出... 再见！")
            break

        else:
            print(f"\n无效选项 '{choice}'。请输入 1, 2, 或 Q。")


if __name__ == "__main__":
    # 检查配置文件是否存在
    if not os.path.exists('config.ini'):
        print("错误: 缺少 `config.ini` 文件。")
        print("请根据模板创建并配置 `config.ini`。")
        sys.exit(1)

    # 检查 package-lock.json 是否存在 (仅在下载时需要，但最好提前提示)
    if not os.path.exists('package-lock.json'):
        print("警告: 未在当前目录找到 `package-lock.json`。")
        print("如果您选择 '1. 下载'，程序将会失败。")

    main()