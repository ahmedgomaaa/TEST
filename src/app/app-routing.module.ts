import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { AboutUsComponent } from './about-us/about-us.component';
import { ContactUsComponent } from './contact-us/contact-us.component';
import { GalleryComponent } from './gallery/gallery.component';
import { HomeComponent } from './home/home.component';

const routes: Routes = [

    {path: 'gallery' , component: GalleryComponent},
    {path: 'about-us' , component: AboutUsComponent},
    {path: 'contact-us' , component: ContactUsComponent},
    {path: 'home' , component: HomeComponent},
    {path: '', redirectTo: '/home', pathMatch: 'full'}
 
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule { }
