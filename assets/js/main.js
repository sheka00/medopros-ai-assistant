document.addEventListener('DOMContentLoaded', () => {
    // Initialize Lucide icons
    const initIcons = () => {
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    };

    initIcons();
    // Fallback for slower mobiles
    setTimeout(initIcons, 500);
    setTimeout(initIcons, 2000);

    // Scroll reveal logic
    const observerOptions = {
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('reveal');
            }
        });
    }, observerOptions);

    document.querySelectorAll('.glass, .feature-card, .step-item').forEach(el => {
        el.classList.add('reveal-init');
        observer.observe(el);
    });

    // Mobile Menu Toggle
    const menuBtn = document.querySelector('.mobile-menu-btn');
    const closeBtn = document.querySelector('.close-menu');
    const overlay = document.querySelector('.mobile-nav-overlay');
    const navLinks = document.querySelectorAll('.mobile-nav-links a');

    if (menuBtn && overlay) {
        menuBtn.addEventListener('click', () => {
            overlay.classList.add('active');
            document.body.style.overflow = 'hidden';
        });
    }

    if (closeBtn && overlay) {
        closeBtn.addEventListener('click', () => {
            overlay.classList.remove('active');
            document.body.style.overflow = 'auto';
        });
    }

    navLinks.forEach(link => {
        link.addEventListener('click', () => {
            overlay.classList.remove('active');
            document.body.style.overflow = 'auto';
        });
    });

    // Contact Form Logic
    const contactForm = document.getElementById('contact-form');
    const formStatus = document.getElementById('form-status');
    
    if (contactForm) {
        contactForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = contactForm.querySelector('button');
            const originalBtnText = btn.textContent;
            
            // Collect data
            const formData = new FormData(contactForm);
            const data = {
                name: formData.get('name'),
                clinic: formData.get('clinic'),
                phone: formData.get('phone')
            };

            try {
                btn.disabled = true;
                btn.textContent = 'Отправка...';
                
                const response = await fetch('/api/contact', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                if (response.ok) {
                    contactForm.reset();
                    formStatus.textContent = 'Заявка успешно отправлена! Мы свяжемся с вами в ближайшее время.';
                    formStatus.className = 'form-status success';
                } else {
                    throw new Error('Ошибка при отправке');
                }
            } catch (err) {
                formStatus.textContent = 'Произошла ошибка. Пожалуйста, попробуйте позже или напишите нам в Telegram.';
                formStatus.className = 'form-status error';
            } finally {
                btn.disabled = false;
                btn.textContent = originalBtnText;
                setTimeout(() => {
                    formStatus.style.opacity = '0';
                    setTimeout(() => {
                        formStatus.textContent = '';
                        formStatus.style.opacity = '1';
                    }, 500);
                }, 5000);
            }
        });
    }

    // Support for video autoplay on mobile
    const video = document.getElementById('hero-demo-video');
    if (video) {
        video.play().catch(() => {
            console.log("Autoplay failed, waiting for user interaction");
        });
    }

    // Header scroll handling
    const header = document.querySelector('.header');
    const handleScroll = () => {
        if (window.scrollY > 100) {
            header.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
        }
    };

    window.addEventListener('scroll', handleScroll);
    handleScroll(); // Initial check
});
