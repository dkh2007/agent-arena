// 依赖源顺序说明（2026-11-20 实测）：
// maven.google.com 在本机不可达（HTTP 000），gradle 官方分发走 services.gradle.org 也会 SSL 失败；
// 因此 google()/mavenCentral() 前面放阿里云镜像，官方源留作兜底（能通时不影响解析）。
pluginManagement {
    repositories {
        maven("https://maven.aliyun.com/repository/google")
        maven("https://maven.aliyun.com/repository/central")
        maven("https://maven.aliyun.com/repository/gradle-plugin")
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven("https://maven.aliyun.com/repository/google")
        maven("https://maven.aliyun.com/repository/central")
        google()
        mavenCentral()
    }
}

rootProject.name = "ArenaCompanion"
include(":app")
